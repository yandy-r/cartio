from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import AuditEvent, CollectionRun, Credential, Customer, Engagement, Job, Site
from .validators import MAX_SEED_HINTS, normalize_seed_hint


class CustomerSerializer(serializers.ModelSerializer):
    compliance = serializers.ListField(
        child=serializers.ChoiceField(choices=Customer.Compliance.choices), required=False
    )

    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "data_mode",
            "live_view_allowed",
            "require_uninspected_path",
            "compliance",
            "notes",
            "created_at",
            "archived_at",
        ]
        read_only_fields = ["created_at", "archived_at"]

    def validate_name(self, value):
        others = Customer.objects.filter(name__iexact=value)
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError("A customer with this name already exists.")
        return value

    def validate_compliance(self, value):
        # Keep the order, drop duplicates.
        return list(dict.fromkeys(value))

    def validate_data_mode(self, value):
        # ADR-0002: changing mode moves or deletes network data, so it needs its own audited
        # admin action. It can't be changed with a plain update.
        if self.instance is not None and value != self.instance.data_mode:
            raise serializers.ValidationError(
                "Data mode is set when the customer is created. Change it with the customer's "
                "data-mode action, which moves its data and logs the change."
            )
        return value

    def validate_live_view_allowed(self, value):
        # ADR-0005: turning live view off has to end open live views, so like data mode it is
        # chosen at creation (onboarding) and changes only through its own audited action.
        if self.instance is not None and value != self.instance.live_view_allowed:
            raise serializers.ValidationError(
                "Live view is set when the customer is created and can't be changed here."
            )
        return value


class CustomerScopedSerializer(serializers.ModelSerializer):
    """Base for records that belong to exactly one customer.

    The customer comes from the URL, never from the request body, and can't change.
    """

    customer = serializers.PrimaryKeyRelatedField(read_only=True)

    def _customer(self) -> Customer:
        return self.instance.customer if self.instance is not None else self.context["customer"]

    def validate_name(self, value):
        others = self.Meta.model.objects.for_customer(self._customer()).filter(name=value)
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError("This customer already has one with this name.")
        return value


class EngagementSerializer(CustomerScopedSerializer):
    class Meta:
        model = Engagement
        fields = [
            "id",
            "customer",
            "name",
            "status",
            "start_date",
            "end_date",
            "assessment",
            "created_at",
            "archived_at",
        ]
        read_only_fields = ["created_at", "archived_at"]

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end < start:
            raise serializers.ValidationError({"end_date": "Must be on or after the start date."})
        return attrs


class SiteSerializer(CustomerScopedSerializer):
    seed_hints = serializers.ListField(
        child=serializers.CharField(max_length=255),
        max_length=MAX_SEED_HINTS,
        required=False,
    )

    class Meta:
        model = Site
        fields = ["id", "customer", "name", "location", "seed_hints", "created_at", "archived_at"]
        read_only_fields = ["created_at", "archived_at"]

    def validate_seed_hints(self, value):
        try:
            hints = [normalize_seed_hint(hint) for hint in value]
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        # Keep the order, drop duplicates.
        return list(dict.fromkeys(hints))


SNMP_AUTH_PROTOCOLS = {"md5", "sha", "sha224", "sha256", "sha384", "sha512"}
SNMP_PRIV_PROTOCOLS = {"des", "aes", "aes192", "aes256"}


class CredentialSerializer(CustomerScopedSerializer):
    """Credentials are written with a `secret` and never read back (ADR-0003)."""

    secret = serializers.DictField(
        child=serializers.CharField(max_length=16384, trim_whitespace=False, allow_blank=True),
        write_only=True,
        required=False,
    )

    class Meta:
        model = Credential
        fields = [
            "id",
            "customer",
            "name",
            "kind",
            "username",
            "auth_protocol",
            "priv_protocol",
            "secret",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate_kind(self, value):
        if self.instance is not None and value != self.instance.kind:
            raise serializers.ValidationError(
                "A credential's kind can't change. Delete it and create a new one."
            )
        return value

    def validate(self, attrs):
        if self._customer().data_mode != Customer.DataMode.HOSTED:
            # ADR-0003: on-site customers keep credentials on their own collector or store.
            raise serializers.ValidationError(
                "This customer keeps its data on site, so its credentials stay there too."
            )
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        if kind is None:
            raise serializers.ValidationError({"kind": "This field is required."})

        def value(name):
            return attrs.get(name, getattr(self.instance, name, ""))

        errors = {}
        if kind in Credential.USERNAME_KINDS and not value("username"):
            errors["username"] = "This kind of credential needs a username."
        if kind not in Credential.USERNAME_KINDS and value("username"):
            errors["username"] = "This kind of credential has no username."
        for name, allowed in (
            ("auth_protocol", SNMP_AUTH_PROTOCOLS),
            ("priv_protocol", SNMP_PRIV_PROTOCOLS),
        ):
            protocol = value(name)
            if kind == Credential.Kind.SNMP_V3 and protocol not in allowed:
                errors[name] = f"Choose one of {', '.join(sorted(allowed))}."
            elif kind != Credential.Kind.SNMP_V3 and protocol:
                errors[name] = "Only SNMPv3 credentials have protocols."

        secret = attrs.get("secret")
        if secret is None and self.instance is None:
            errors["secret"] = "This field is required."  # noqa: S105 (a message, not a secret)
        elif secret is not None:
            required, optional = Credential.SECRET_FIELDS[kind]
            missing = [name for name in required if not secret.get(name)]
            unknown = sorted(set(secret) - set(required) - set(optional))
            if missing or unknown:
                errors["secret"] = (
                    f"Give {', '.join(required)}"
                    + (f" (optional: {', '.join(optional)})" if optional else "")
                    + "."
                )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class CollectionRunSerializer(serializers.ModelSerializer):
    """A run with its result. `outcome` is "running" until the run has a result."""

    customer = serializers.PrimaryKeyRelatedField(read_only=True)
    finished_at = serializers.SerializerMethodField()
    outcome = serializers.SerializerMethodField()
    device_count = serializers.SerializerMethodField()
    error_summary = serializers.SerializerMethodField()

    class Meta:
        model = CollectionRun
        fields = [
            "id",
            "customer",
            "engagement",
            "collector",
            "job_type",
            "started_by",
            "started_at",
            "finished_at",
            "outcome",
            "device_count",
            "error_summary",
        ]
        read_only_fields = fields

    @staticmethod
    def _result(run):
        return getattr(run, "result", None)

    def get_finished_at(self, run):
        result = self._result(run)
        return serializers.DateTimeField().to_representation(result.finished_at) if result else None

    def get_outcome(self, run):
        result = self._result(run)
        return result.outcome if result else "running"

    def get_device_count(self, run):
        result = self._result(run)
        return result.device_count if result else None

    def get_error_summary(self, run):
        result = self._result(run)
        return result.error_summary if result else ""


class AuditEventSerializer(serializers.ModelSerializer):
    customer = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "customer",
            "at",
            "action",
            "actor",
            "collector",
            "object_type",
            "object_id",
            "object_name",
            "detail",
        ]
        read_only_fields = fields


class JobSerializer(serializers.ModelSerializer):
    """A job and its state. Created with a collector, a type, optional engagement, settings and
    credential ids; everything else is the channel's to set."""

    customer = serializers.PrimaryKeyRelatedField(read_only=True)
    collector = serializers.UUIDField(source="collector_id")
    engagement = serializers.IntegerField(
        source="engagement_id", required=False, allow_null=True, min_value=1
    )
    type = serializers.ChoiceField(choices=Job.Type.choices)
    params = serializers.DictField(required=False)
    credentials = serializers.ListField(
        child=serializers.UUIDField(), required=False, write_only=True, max_length=100
    )

    class Meta:
        model = Job
        fields = [
            "id",
            "customer",
            "collector",
            "engagement",
            "type",
            "params",
            "credentials",
            "credential_ids",
            "status",
            "run",
            "created_by",
            "created_at",
            "started_at",
            "finished_at",
            "progress_message",
            "progress_percent",
            "result_id",
            "result_size",
        ]
        read_only_fields = [
            "credential_ids",
            "status",
            "run",
            "created_by",
            "created_at",
            "started_at",
            "finished_at",
            "progress_message",
            "progress_percent",
            "result_id",
            "result_size",
        ]

    def validate_type(self, value):
        if value == Job.Type.EXPORT:
            raise serializers.ValidationError(
                "An export is started by changing the customer's data mode, not queued directly."
            )
        return value


class DataModeChangeSerializer(serializers.Serializer):
    """A data mode change (YAN-1712): to On site through a collector, or back to Hosted."""

    to = serializers.ChoiceField(choices=Customer.DataMode.choices)
    # To On site: the collector whose on-site store takes the export.
    collector = serializers.UUIDField(required=False)
    live_view_allowed = serializers.BooleanField(required=False, default=False)
    # To Hosted: who at the customer approved keeping their network data on the controller.
    confirmed_by = serializers.CharField(required=False, allow_blank=True, max_length=150)

    def validate(self, attrs):
        if attrs["to"] == Customer.DataMode.ON_SITE and "collector" not in attrs:
            raise serializers.ValidationError(
                {"collector": "Name the collector whose on-site store takes the export."}
            )
        if attrs["to"] == Customer.DataMode.HOSTED and not attrs.get("confirmed_by", "").strip():
            raise serializers.ValidationError(
                {"confirmed_by": "Name who at the customer approved moving to Hosted."}
            )
        return attrs
