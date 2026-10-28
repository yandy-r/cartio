import hashlib
import hmac
import secrets
import uuid

from django.db import models
from django.db.models.functions import Lower

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


def _customer_pk(customer) -> int:
    pk = getattr(customer, "pk", customer)
    if pk is None:
        raise UnscopedQueryError("The customer has no id yet. Save it first.")
    return pk


class CustomerScopedQuerySet(models.QuerySet):
    """Rows of exactly one customer. Records created through it belong to that customer."""

    _customer_id = None

    def _clone(self):
        clone = super()._clone()
        clone._customer_id = self._customer_id
        return clone

    def create(self, **kwargs):
        if self._customer_id is None:
            raise UnscopedQueryError("Create records through .for_customer(customer).")
        given = kwargs.pop("customer", None)
        given_id = kwargs.pop("customer_id", None)
        for other in (given, given_id):
            if other is not None and _customer_pk(other) != self._customer_id:
                raise CrossCustomerError("This record belongs to another customer.")
        return super().create(customer_id=self._customer_id, **kwargs)


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

    Isolation is enforced here, below the API: `objects` only answers queries scoped to one
    customer, and saving a record that points at another customer's record fails.
    Queryset `update()` and `bulk_create()` skip `save()`, so scope them with `for_customer()`.
    """

    customer = models.ForeignKey("Customer", on_delete=models.PROTECT, related_name="%(class)ss")

    # Declared first, so Django's own machinery (admin, related managers) uses it.
    all_customers = models.Manager()
    objects = CustomerScopedManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.check_customer_links()
        super().save(*args, **kwargs)

    def check_customer_links(self) -> None:
        """Take the customer from a parent record, and refuse a parent of another customer."""
        for field in self._meta.concrete_fields:
            if not (field.many_to_one and issubclass(field.related_model, CustomerOwned)):
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

    class Meta:
        ordering = ["customer__name", "-start_date", "name"]
        constraints = [
            models.UniqueConstraint(fields=["customer", "name"], name="unique_engagement_name"),
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

    class Meta:
        ordering = ["customer__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["customer", "name"], name="unique_site_name"),
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

    class Meta:
        ordering = ["customer__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["customer", "name"], name="unique_collector_name"),
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

    class Meta:
        ordering = ["-at"]
        get_latest_by = "at"
