import hashlib
import hmac
import json
import secrets
import uuid

from django.db import IntegrityError, models, transaction
from django.db.models.functions import Length, Lower
from django.db.models.lookups import Exact
from django.views.decorators.debug import sensitive_variables

from .validators import validate_seed_hints


def _hash(value: str) -> str:
    # Tokens and secrets are 32 random bytes, so a plain SHA-256 is enough to store them.
    return hashlib.sha256(value.encode()).hexdigest()


class Archivable(models.Model):
    """Records are archived, never deleted, so history and audit trails stay intact."""

    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class UnscopedQueryError(RuntimeError):
    """A query on customer-owned rows that didn't name a customer."""


class CrossCustomerError(ValueError):
    """A record that would link two customers' data."""


class OnSiteDataError(RuntimeError):
    """Network data for an On site customer, which the controller never stores (ADR-0002)."""


class AppendOnlyError(RuntimeError):
    """A change to a record that may only ever be added, such as an audit event."""


def _customer_pk(customer) -> int:
    pk = getattr(customer, "pk", customer)
    if pk is None:
        raise UnscopedQueryError("The customer has no id yet. Save it first.")
    return Customer._meta.pk.to_python(pk)


def _link_fields(model) -> set[str]:
    """Names of the fields that tie a record to a customer: `customer` and parent records."""
    names = set()
    for field in model._meta.concrete_fields:
        if field.name == "customer" or (
            (field.many_to_one or field.one_to_one)
            and issubclass(field.related_model, CustomerOwned)
        ):
            names |= {field.name, field.attname}
    return names


class CustomerOwnedQuerySet(models.QuerySet):
    """Customer-owned rows. Bulk writes can't move rows to another customer or relink them."""

    def update(self, **kwargs):
        self._refuse_append_only_change()
        self._refuse_bulk_relink(kwargs)
        return super().update(**kwargs)

    def delete(self):
        self._refuse_append_only_change()
        return super().delete()

    def _refuse_append_only_change(self) -> None:
        if self.model.append_only:
            raise AppendOnlyError(f"{self.model.__name__} records can't be changed or deleted.")

    def bulk_update(self, objs, fields, *args, **kwargs):
        self._refuse_append_only_change()
        self._refuse_bulk_relink(fields)
        return super().bulk_update(objs, fields, *args, **kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        if kwargs.get("update_conflicts"):
            self._refuse_append_only_change()
        self._refuse_bulk_relink(kwargs.get("update_fields") or ())
        for obj in objs:
            obj.check_customer_links()
            obj.set_derived_fields()
        return super().bulk_create(objs, *args, **kwargs)

    def _refuse_bulk_relink(self, fields) -> None:
        if _link_fields(self.model) & set(fields):
            raise CrossCustomerError(
                f"A {self.model.__name__}'s customer and parent records can't change in bulk. "
                f"Save each record instead."
            )


class CustomerScopedQuerySet(CustomerOwnedQuerySet):
    """Rows of exactly one customer. Records created through it belong to that customer."""

    _customer_id = None

    def _clone(self):
        clone = super()._clone()
        clone._customer_id = self._customer_id
        return clone

    def create(self, **kwargs):
        self._require_scope()
        given = kwargs.pop("customer", None)
        given_id = kwargs.pop("customer_id", None)
        for other in (given, given_id):
            if other is not None and _customer_pk(other) != self._customer_id:
                raise CrossCustomerError("This record belongs to another customer.")
        return super().create(customer_id=self._customer_id, **kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        self._require_scope()
        objs = list(objs)
        for obj in objs:
            if obj.customer_id is None:
                obj.customer_id = self._customer_id
            elif _customer_pk(obj.customer_id) != self._customer_id:
                raise CrossCustomerError("This record belongs to another customer.")
        return super().bulk_create(objs, *args, **kwargs)

    def _require_scope(self) -> None:
        if self._customer_id is None:
            raise UnscopedQueryError("Create records through .for_customer(customer).")


class CustomerScopedManager(models.Manager.from_queryset(CustomerScopedQuerySet)):
    """`Model.objects`: every query must name its customer with `.for_customer()`.

    Code that really needs every customer's rows (the Django admin, the enrollment token
    lookup) says so by using `Model.all_customers` instead.
    """

    def get_queryset(self):
        raise UnscopedQueryError(
            f"{self.model.__name__} rows belong to one customer. Query them with "
            f".objects.for_customer(customer), or .all_customers on an explicit admin path."
        )

    def for_customer(self, customer) -> CustomerScopedQuerySet:
        customer_id = _customer_pk(customer)
        queryset = super().get_queryset().filter(customer_id=customer_id)
        queryset._customer_id = customer_id
        return queryset


class Customer(Archivable):
    """An organization we assess. No data, collector or credential crosses customers."""

    class DataMode(models.TextChoices):
        # ADR-0002: where this customer's network data is stored.
        HOSTED = "hosted", "Hosted"
        ON_SITE = "on_site", "On site"

    name = models.CharField(max_length=200, unique=True)
    data_mode = models.CharField(max_length=16, choices=DataMode, default=DataMode.HOSTED)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="unique_customer_name_ci"),
            models.CheckConstraint(
                condition=models.Q(data_mode__in=["hosted", "on_site"]),
                name="customer_data_mode_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class CustomerOwned(models.Model):
    """Base for every record that belongs to one customer (all models except Customer).

    Isolation is enforced here, below the API, and again by PostgreSQL itself (`db_guards`):

    - `objects` only answers queries scoped to one customer with `for_customer()`.
    - A record's customer never changes once saved.
    - Saving a record that points at another customer's record fails.
    - Bulk writes (`update()`, `bulk_update()`, related-manager `add()`) can't change a
      record's customer or parent records.
    """

    customer = models.ForeignKey("Customer", on_delete=models.PROTECT, related_name="%(class)ss")

    # Audit records: added once, never changed or deleted.
    append_only = False

    # Network data (devices, outputs, configs). An On site customer keeps it in their own store,
    # so the controller refuses to save it for them (ADR-0002, ADR-0005).
    network_data = False

    # Declared first, so Django's own machinery (admin, related managers) uses it.
    all_customers = models.Manager.from_queryset(CustomerOwnedQuerySet)()
    objects = CustomerScopedManager()

    class Meta:
        abstract = True
        # Related-manager writes and deletes go through the guarded queryset too.
        base_manager_name = "all_customers"

    @classmethod
    def from_db(cls, db, field_names, values, **kwargs):
        instance = super().from_db(db, field_names, values, **kwargs)
        instance._saved_customer_id = instance.__dict__.get("customer_id")
        return instance

    def save(self, *args, **kwargs):
        if self.append_only:
            if not self._state.adding:
                raise AppendOnlyError(f"{type(self).__name__} records can't be changed.")
            # Never an UPDATE, even for a new instance that names an existing pk.
            kwargs["force_insert"] = True
        update_fields = kwargs.get("update_fields")
        if update_fields is None or _link_fields(type(self)) & set(update_fields):
            self.check_customer_links()
        self.set_derived_fields()
        super().save(*args, **kwargs)
        if update_fields is None or {"customer", "customer_id"} & set(update_fields):
            self._saved_customer_id = self.customer_id

    def delete(self, *args, **kwargs):
        if self.append_only:
            raise AppendOnlyError(f"{type(self).__name__} records can't be deleted.")
        return super().delete(*args, **kwargs)

    def _stored_customer_id(self):
        """The customer this row has in the database, or None for a row not saved yet."""
        saved = getattr(self, "_saved_customer_id", None)
        if saved is None and self.pk is not None:
            # Loaded without its customer (`only()`, `defer()`) or built in memory with a pk.
            saved = (
                type(self)
                .all_customers.filter(pk=self.pk)
                .values_list("customer_id", flat=True)
                .first()
            )
        return saved

    def set_derived_fields(self) -> None:
        """Fill fields computed from others, such as a digest. Runs on save and bulk create."""

    def check_customer_links(self) -> None:
        """Take the customer from a parent record, and refuse a parent of another customer."""
        saved = self._stored_customer_id()
        if saved is not None and self.customer_id != saved:
            raise CrossCustomerError("A record can't move to another customer.")
        for field in self._meta.concrete_fields:
            if not (field.many_to_one or field.one_to_one) or not issubclass(
                field.related_model, CustomerOwned
            ):
                continue
            parent = getattr(self, field.name)
            if parent is None:
                continue
            if self.customer_id is None:
                self.customer_id = parent.customer_id
            elif parent.customer_id != self.customer_id:
                raise CrossCustomerError(
                    f"{field.verbose_name} belongs to another customer than this record."
                )
        if self.network_data and self.customer_id is not None:
            data_mode = (
                Customer.objects.filter(pk=self.customer_id)
                .values_list("data_mode", flat=True)
                .first()
            )
            if data_mode == Customer.DataMode.ON_SITE:
                raise OnSiteDataError(
                    f"This customer is On site: its {self._meta.verbose_name_plural} stay in "
                    "the customer's own store, not on the controller."
                )


class Engagement(Archivable, CustomerOwned):
    """One assessment we run for a customer."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"

    name = models.CharField(max_length=200)
    status = models.CharField(max_length=16, choices=Status, default=Status.PLANNED)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    # The assessment this engagement represents: its scope and goal, in words.
    assessment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["customer__name", "-start_date", "name"]
        constraints = [
            models.UniqueConstraint(fields=["customer", "name"], name="unique_engagement_name"),
            # The target of the database-level customer ties (db_guards).
            models.UniqueConstraint(fields=["id", "customer"], name="engagement_id_customer"),
            models.CheckConstraint(
                condition=models.Q(status__in=["planned", "active", "completed"]),
                name="engagement_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(start_date__isnull=True)
                | models.Q(end_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="engagement_ends_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.customer} / {self.name}"


class Site(Archivable, CustomerOwned):
    """A place in a customer's network: an office, a data center, a cloud region."""

    name = models.CharField(max_length=200)
    location = models.CharField(max_length=500, blank=True)
    # Where discovery starts: IP addresses, networks (CIDR) or host names.
    seed_hints = models.JSONField(default=list, blank=True, validators=[validate_seed_hints])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["customer__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["customer", "name"], name="unique_site_name"),
            models.UniqueConstraint(fields=["id", "customer"], name="site_id_customer"),
        ]

    def __str__(self) -> str:
        return f"{self.customer} / {self.name}"


class Collector(CustomerOwned):
    """An agent running in or near one customer's network.

    A collector belongs to exactly one customer. Collector endpoints reach data only through
    `collector.scoped()`, so a collector can't see another customer's jobs, credentials or data.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    # Set when the collector is created, cleared when it enrolls.
    enrollment_token_hash = models.CharField(max_length=64, blank=True, db_index=True)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    secret_hash = models.CharField(max_length=64, blank=True)

    hostname = models.CharField(max_length=255, blank=True)
    version = models.CharField(max_length=50, blank=True)
    platform = models.CharField(max_length=200, blank=True)
    last_check_in = models.DateTimeField(null=True, blank=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["customer__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["customer", "name"], name="unique_collector_name"),
            models.UniqueConstraint(fields=["id", "customer"], name="collector_id_customer"),
        ]

    def __str__(self) -> str:
        return f"{self.customer} / {self.name}"

    def issue_enrollment_token(self) -> str:
        """Return a new one-time enrollment token. Only its hash is stored."""
        # Hex, so the token never starts with "-" and is safe to pass on a command line.
        token = secrets.token_hex(32)
        self.enrollment_token_hash = _hash(token)
        return token

    def issue_secret(self) -> str:
        """Return a new collector secret. Only its hash is stored."""
        secret = secrets.token_urlsafe(32)
        self.secret_hash = _hash(secret)
        return secret

    def check_secret(self, secret: str) -> bool:
        return bool(self.secret_hash) and hmac.compare_digest(self.secret_hash, _hash(secret))

    def scoped(self, model: type[CustomerOwned]) -> CustomerScopedQuerySet:
        """The rows of `model` this collector may see: its own customer's, and no others."""
        return model.objects.for_customer(self.customer_id)

    @classmethod
    def for_enrollment_token(cls, token: str) -> "Collector | None":
        # Unscoped on purpose: the token is what tells us the customer.
        return cls.all_customers.filter(enrollment_token_hash=_hash(token)).first()

    @classmethod
    def for_id(cls, collector_id: str) -> "Collector | None":
        # Unscoped on purpose: the collector's id is what tells us the customer.
        try:
            uuid.UUID(collector_id)
        except ValueError:
            return None
        return cls.all_customers.filter(pk=collector_id).first()


class CheckIn(CustomerOwned):
    """One check-in from a collector. Kept as a record of when each collector was seen."""

    collector = models.ForeignKey(Collector, on_delete=models.CASCADE, related_name="check_ins")
    at = models.DateTimeField(auto_now_add=True)
    hostname = models.CharField(max_length=255)
    version = models.CharField(max_length=50)
    platform = models.CharField(max_length=200)

    class Meta(CustomerOwned.Meta):
        ordering = ["-at"]
        get_latest_by = "at"


class CustomerDataKey(CustomerOwned):
    """A customer's data key, stored only wrapped by the master key (ADR-0003)."""

    wrapped_key = models.BinaryField()
    master_key_id = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(CustomerOwned.Meta):
        constraints = [
            models.UniqueConstraint(fields=["customer"], name="one_data_key_per_customer"),
        ]

    def __str__(self) -> str:
        return f"Data key for {self.customer}"

    @staticmethod
    def _context(customer_id) -> bytes:
        return f"cartio data key / customer {customer_id}".encode()

    @classmethod
    @sensitive_variables("data_key")
    def key_for(cls, customer_id) -> bytes:
        """The customer's plain data key, created on first use. Callers keep it in memory only."""
        from . import vault

        context = cls._context(customer_id)
        keys = cls.objects.for_customer(customer_id)
        record = keys.first()
        if record is None:
            master = vault.master_key()
            data_key = vault.new_data_key()
            try:
                with transaction.atomic():
                    keys.create(
                        wrapped_key=master.wrap(data_key, context), master_key_id=master.key_id
                    )
                return data_key
            except IntegrityError:
                # Another request created it first. Use theirs.
                record = keys.get()
        # The current master key, or the previous one while a rotation is under way. Any other
        # key is a clear error naming the key that's missing.
        return vault.master_key_for(record.master_key_id).unwrap(record.wrapped_key, context)


class Credential(CustomerOwned):
    """A device credential of one customer. Its secret is encrypted and never shown back."""

    class Kind(models.TextChoices):
        SSH_PASSWORD = "ssh_password", "SSH user and password"
        SSH_KEY = "ssh_key", "SSH user and private key"
        SNMP_V2C = "snmp_v2c", "SNMPv2c community"
        SNMP_V3 = "snmp_v3", "SNMPv3 user"
        API_TOKEN = "api_token", "API token"

    # The secret fields of each kind: (required, optional).
    SECRET_FIELDS = {
        "ssh_password": (("password",), ()),
        "ssh_key": (("private_key",), ("passphrase",)),
        "snmp_v2c": (("community",), ()),
        "snmp_v3": (("auth_key", "priv_key"), ()),
        "api_token": (("token",), ()),
    }
    USERNAME_KINDS = {"ssh_password", "ssh_key", "snmp_v3"}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=16, choices=Kind)
    # Not secret: shown back so engineers can tell credentials apart.
    username = models.CharField(max_length=200, blank=True)
    auth_protocol = models.CharField(max_length=16, blank=True)
    priv_protocol = models.CharField(max_length=16, blank=True)
    ciphertext = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["customer__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["customer", "name"], name="unique_credential_name"),
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=["ssh_password", "ssh_key", "snmp_v2c", "snmp_v3", "api_token"]
                ),
                name="credential_kind_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.customer} / {self.name}"

    def __repr__(self) -> str:
        return f"<Credential {self.pk} {self.kind}>"

    def _context(self) -> bytes:
        # Binds the ciphertext to this row, customer and kind, so it can't be copied elsewhere.
        return f"cartio credential / customer {self.customer_id} / {self.pk} / {self.kind}".encode()

    @sensitive_variables("values", "payload")
    def set_secret(self, values: dict[str, str]) -> None:
        """Encrypt `values` (the kind's secret fields) under the customer's data key."""
        from . import vault

        required, optional = self.SECRET_FIELDS[self.kind]
        missing = [name for name in required if not values.get(name)]
        unknown = set(values) - set(required) - set(optional)
        if missing or unknown:
            raise ValueError(f"secret fields: missing {missing}, unknown {sorted(unknown)}")
        payload = json.dumps({k: v for k, v in values.items() if v}).encode()
        self.ciphertext = vault.encrypt(
            CustomerDataKey.key_for(self.customer_id), payload, self._context()
        )

    @sensitive_variables("key")
    def secret(self) -> dict[str, str]:
        """The decrypted secret. Only the delivery path calls this, never the API."""
        from . import vault

        key = CustomerDataKey.key_for(self.customer_id)
        return json.loads(vault.decrypt(key, self.ciphertext, self._context()))


class AuditEvent(CustomerOwned):
    """An append-only record of something done to a customer's data: who, what, when, where.

    This is the customer's audit log (YAN-1654): admin actions, credential use and collection
    runs all write here, and `audit.export_events()` hands it out for compliance requests.
    Sign-ins belong to no customer, so they go to `LoginEvent` instead.
    """

    append_only = True

    class Action(models.TextChoices):
        # Customers, engagements and sites. `object_type` says which.
        RECORD_CREATED = "record.created", "Created"
        RECORD_UPDATED = "record.updated", "Updated"
        RECORD_ARCHIVED = "record.archived", "Archived"
        RECORD_RESTORED = "record.restored", "Restored"
        COLLECTOR_CREATED = "collector.created", "Collector created"
        COLLECTOR_ENROLLED = "collector.enrolled", "Collector enrolled"
        CREDENTIAL_CREATED = "credential.created", "Credential created"
        CREDENTIAL_UPDATED = "credential.updated", "Credential updated"
        CREDENTIAL_DELETED = "credential.deleted", "Credential deleted"
        CREDENTIAL_USED = "credential.used", "Credential delivered to a collector"
        RUN_STARTED = "run.started", "Collection run started"
        RUN_FINISHED = "run.finished", "Collection run finished"
        AUDIT_EXPORTED = "audit.exported", "Audit log exported"
        MASTER_KEY_ROTATED = "master_key.rotated", "Data key rewrapped by a new master key"
        OUTPUTS_STORED = "outputs.stored", "Device outputs stored and parsed"
        SOURCE_REPARSED = "source.reparsed", "Source outputs parsed again"

    at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=64, choices=Action)
    # Usernames, not foreign keys, so the record outlives the account.
    actor = models.CharField(max_length=150, blank=True)
    collector = models.ForeignKey(
        Collector, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events"
    )
    object_type = models.CharField(max_length=64)
    object_id = models.CharField(max_length=64)
    # A name for people reading the log. Never a secret.
    object_name = models.CharField(max_length=200, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["-at"]

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.object_name}"


class CollectionRun(CustomerOwned):
    """One job a collector ran for a customer: what, where, and who started it.

    Append-only. The outcome is written once, as the run's `CollectionRunResult`, so a run's
    record is never edited. A run without a result is still running (or was lost).
    """

    append_only = True

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # The kind of job, such as "discovery" or "config_collection". Named by the job channel.
    job_type = models.CharField(max_length=64)
    engagement = models.ForeignKey(
        Engagement, null=True, blank=True, on_delete=models.PROTECT, related_name="runs"
    )
    collector = models.ForeignKey(Collector, on_delete=models.PROTECT, related_name="runs")
    # A username, not a foreign key, so the record outlives the account. Blank for scheduled runs.
    started_by = models.CharField(max_length=150, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(fields=["id", "customer"], name="collection_run_id_customer"),
        ]

    def __str__(self) -> str:
        return f"{self.started_at:%Y-%m-%d %H:%M} {self.job_type} on {self.collector}"


class CollectionRunResult(CustomerOwned):
    """How a collection run ended. Added once per run, never changed."""

    append_only = True

    class Outcome(models.TextChoices):
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partly succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    MAX_ERROR_SUMMARY = 2000

    # The primary key, so the database itself refuses a second result for a run.
    run = models.OneToOneField(
        CollectionRun, primary_key=True, on_delete=models.PROTECT, related_name="result"
    )
    finished_at = models.DateTimeField(auto_now_add=True)
    outcome = models.CharField(max_length=16, choices=Outcome)
    MAX_DEVICE_COUNT = 2**31 - 1
    device_count = models.PositiveIntegerField(default=0)
    # A short reason for people reading the log. Never device output or a secret.
    error_summary = models.CharField(max_length=MAX_ERROR_SUMMARY, blank=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["-finished_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(outcome__in=["succeeded", "partial", "failed", "cancelled"]),
                name="run_outcome_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run}: {self.outcome}"


# The normalized device model (YAN-1656, ADR-0008). Every record belongs to one engagement, so
# each assessment keeps its own copy of the network as it was found, and the next engagement's
# diff compares two copies. Field names follow NetBox where one fits, for a later export.
#
# Device output and configs arrive masked (ADR-0003, `cartio_core.masking`): the collector masks
# them before upload. Nothing here holds a secret in clear text.


class CrossEngagementError(ValueError):
    """A record that would mix two engagements' data."""


def _check_same_engagement(*engagement_ids) -> None:
    if len({pk for pk in engagement_ids if pk is not None}) > 1:
        raise CrossEngagementError("These records belong to different engagements.")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Source(CustomerOwned):
    """How a batch of data arrived for an engagement: a live run, an upload or a tool export.

    Append-only, so every device, output and config can always be traced to where it came from.
    """

    append_only = True
    network_data = True

    class Kind(models.TextChoices):
        LIVE_RUN = "live_run", "Live collection run"
        UPLOAD = "upload", "Uploaded file"
        TOOL_EXPORT = "tool_export", "Customer tool export"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="sources")
    kind = models.CharField(max_length=16, choices=Kind)
    # Set for live runs.
    run = models.ForeignKey(
        CollectionRun, null=True, blank=True, on_delete=models.PROTECT, related_name="sources"
    )
    # A file name or a short label for people. Never device output.
    name = models.CharField(max_length=255, blank=True)
    # A username, not a foreign key, so the record outlives the account.
    added_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["id", "customer"], name="source_id_customer"),
            models.CheckConstraint(
                condition=models.Q(kind__in=["live_run", "upload", "tool_export"]),
                name="source_kind_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind="live_run") | models.Q(run__isnull=False),
                name="source_live_run_has_run",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.name}".strip()

    def check_customer_links(self) -> None:
        super().check_customer_links()
        if self.run_id is not None and self.run.engagement_id != self.engagement_id:
            raise CrossEngagementError("A live run's source belongs to the run's engagement.")


class Device(CustomerOwned):
    """A network device as found in one engagement."""

    network_data = True

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="devices")
    site = models.ForeignKey(
        Site, null=True, blank=True, on_delete=models.PROTECT, related_name="devices"
    )
    # Where the device was first found in this engagement.
    source = models.ForeignKey(
        Source, null=True, blank=True, on_delete=models.PROTECT, related_name="devices"
    )
    # The host name, unique within the engagement.
    name = models.CharField(max_length=255)
    # A `cartio_core.masking.Platform` value, such as "ios" or "fortios". Blank until known.
    platform = models.CharField(max_length=32, blank=True)
    manufacturer = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    serial = models.CharField(max_length=100, blank=True)
    os_version = models.CharField(max_length=100, blank=True)
    management_ip = models.GenericIPAddressField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["engagement", "name"], name="unique_device_name"),
            models.UniqueConstraint(fields=["id", "customer"], name="device_id_customer"),
        ]

    def __str__(self) -> str:
        return self.name

    def check_customer_links(self) -> None:
        super().check_customer_links()
        if self.source_id is not None:
            _check_same_engagement(self.engagement_id, self.source.engagement_id)
        if self.pk is not None and not self._state.adding:
            saved = (
                Device.all_customers.filter(pk=self.pk)
                .values_list("engagement_id", flat=True)
                .first()
            )
            # Its interfaces, outputs and configs stay with the engagement they were found in.
            if saved is not None and saved != self.engagement_id:
                raise CrossEngagementError("A device can't move to another engagement.")


class Interface(CustomerOwned):
    """A port or logical interface of a device."""

    network_data = True

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="interfaces")
    name = models.CharField(max_length=64)
    description = models.CharField(max_length=255, blank=True)
    # Unknown until parsed: null rather than a guess.
    enabled = models.BooleanField(null=True, blank=True)
    mac_address = models.CharField(max_length=17, blank=True)
    mtu = models.PositiveIntegerField(null=True, blank=True)
    # In kbit/s, as NetBox stores it.
    speed = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["device__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["device", "name"], name="unique_interface_name"),
            models.UniqueConstraint(fields=["id", "customer"], name="interface_id_customer"),
        ]

    def __str__(self) -> str:
        return f"{self.device} {self.name}"


class Link(CustomerOwned):
    """A neighbor relation for the map: interface A to interface B, or to a device not in scope.

    A neighbor that isn't in the inventory (out of scope, or not reached yet) has no `b_interface`
    and keeps the names it announced instead.
    """

    network_data = True

    class Protocol(models.TextChoices):
        LLDP = "lldp", "LLDP"
        CDP = "cdp", "CDP"
        OTHER = "other", "Other"

    a_interface = models.ForeignKey(Interface, on_delete=models.CASCADE, related_name="links_a")
    b_interface = models.ForeignKey(
        Interface, null=True, blank=True, on_delete=models.CASCADE, related_name="links_b"
    )
    b_device_name = models.CharField(max_length=255, blank=True)
    b_interface_name = models.CharField(max_length=64, blank=True)
    protocol = models.CharField(max_length=8, choices=Protocol, default=Protocol.OTHER)
    source = models.ForeignKey(
        Source, null=True, blank=True, on_delete=models.PROTECT, related_name="links"
    )

    class Meta(CustomerOwned.Meta):
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(a_interface=models.F("b_interface")),
                name="link_not_to_itself",
            ),
            models.CheckConstraint(
                condition=models.Q(protocol__in=["lldp", "cdp", "other"]),
                name="link_protocol_valid",
            ),
        ]

    def __str__(self) -> str:
        far = self.b_interface or f"{self.b_device_name} {self.b_interface_name}".strip()
        return f"{self.a_interface} - {far}"

    def check_customer_links(self) -> None:
        super().check_customer_links()
        if self.a_interface_id is not None:
            _check_same_engagement(
                self.a_interface.device.engagement_id,
                self.b_interface.device.engagement_id if self.b_interface_id else None,
                self.source.engagement_id if self.source_id else None,
            )


class RawOutput(CustomerOwned):
    """One command's output as collected or uploaded, kept so parsers can be re-run.

    Append-only. Masked before it leaves the collector, like configs.
    """

    append_only = True
    network_data = True

    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="raw_outputs")
    # Null when an upload's device isn't known until it's parsed.
    device = models.ForeignKey(
        Device, null=True, blank=True, on_delete=models.PROTECT, related_name="raw_outputs"
    )
    platform = models.CharField(max_length=32, blank=True)
    # The allow-listed command, or for an upload the command the file says it holds.
    command = models.CharField(max_length=255)
    output = models.TextField()
    sha256 = models.CharField(max_length=64, editable=False)
    collected_at = models.DateTimeField(auto_now_add=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["collected_at"]
        constraints = [
            # Set by set_derived_fields(). Raw SQL that skips it fails here.
            models.CheckConstraint(
                condition=Exact(Length("sha256"), 64), name="raw_output_has_digest"
            ),
        ]

    def __str__(self) -> str:
        return self.command

    def check_customer_links(self) -> None:
        super().check_customer_links()
        if self.device_id is not None and self.source_id is not None:
            _check_same_engagement(self.source.engagement_id, self.device.engagement_id)

    def set_derived_fields(self) -> None:
        self.sha256 = _sha256(self.output)


class ConfigSnapshot(CustomerOwned):
    """A device's masked configuration as collected in one engagement.

    Append-only: a re-collection adds a snapshot, and the latest one is the device's config.
    """

    append_only = True
    network_data = True

    device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="config_snapshots")
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="config_snapshots")
    content = models.TextField()
    # Of the masked content, so an unchanged config is easy to spot.
    sha256 = models.CharField(max_length=64, editable=False)
    collected_at = models.DateTimeField(auto_now_add=True)

    class Meta(CustomerOwned.Meta):
        ordering = ["-collected_at"]
        get_latest_by = "collected_at"
        constraints = [
            models.CheckConstraint(
                condition=Exact(Length("sha256"), 64), name="config_snapshot_has_digest"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.device} {self.collected_at:%Y-%m-%d %H:%M}"

    def check_customer_links(self) -> None:
        super().check_customer_links()
        if self.device_id is not None and self.source_id is not None:
            _check_same_engagement(self.device.engagement_id, self.source.engagement_id)

    def set_derived_fields(self) -> None:
        self.sha256 = _sha256(self.content)


class AppendOnlyQuerySet(models.QuerySet):
    """Rows that are only ever added: no update or delete, in bulk or one by one."""

    def _refuse(self, *args, **kwargs):
        raise AppendOnlyError(f"{self.model.__name__} records can't be changed or deleted.")

    update = delete = bulk_update = _refuse

    def bulk_create(self, objs, *args, **kwargs):
        if kwargs.get("update_conflicts"):
            self._refuse()
        return super().bulk_create(objs, *args, **kwargs)


class LoginEvent(models.Model):
    """An append-only record of a sign-in, sign-out or failed sign-in to the controller.

    Sign-ins aren't tied to a customer, so unlike every other record this one isn't
    customer-owned. It never holds a password, only the username that was tried. Its failures
    drive the sign-in limits, and `purge_login_events` drops entries past the retention period.
    """

    class Kind(models.TextChoices):
        LOGGED_IN = "logged_in", "Signed in"
        LOGGED_OUT = "logged_out", "Signed out"
        FAILED = "failed", "Sign-in failed"

    at = models.DateTimeField(auto_now_add=True)
    kind = models.CharField(max_length=16, choices=Kind)
    username = models.CharField(max_length=150, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-at"]
        base_manager_name = "objects"
        # The sign-in limits count recent failures by account and by address.
        indexes = [
            models.Index(fields=["username", "at"], name="login_event_user_at"),
            models.Index(fields=["ip_address", "at"], name="login_event_address_at"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=["logged_in", "logged_out", "failed"]),
                name="login_event_kind_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d %H:%M} {self.kind} {self.username}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("LoginEvent records can't be changed.")
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("LoginEvent records can't be deleted.")
