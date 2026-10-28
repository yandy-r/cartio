import ipaddress
import re

from rest_framework import serializers

from .models import Customer, Engagement, Site

MAX_SEED_HINTS = 256
_HOSTNAME_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "name", "data_mode", "notes", "created_at", "archived_at"]
        read_only_fields = ["created_at", "archived_at"]

    def validate_data_mode(self, value):
        # ADR-0002: changing mode moves or deletes network data, so it needs its own audited
        # admin action. It can't be changed with a plain update.
        if self.instance is not None and value != self.instance.data_mode:
            raise serializers.ValidationError(
                "Data mode is set when the customer is created and can't be changed here."
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
        others = self.Meta.model.objects.filter(customer=self._customer(), name=value)
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
        bad = [hint for hint in value if not _is_seed_hint(hint)]
        if bad:
            raise serializers.ValidationError(
                "Each seed hint must be an IP address, a network in CIDR form or a host name."
            )
        return value


def _is_seed_hint(value: str) -> bool:
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        pass
    else:
        return True
    name = value.rstrip(".")
    labels = name.split(".")
    # An all-numeric last label is a mistyped address, not a host name.
    return (
        0 < len(name) <= 253
        and all(_HOSTNAME_LABEL.match(label) for label in labels)
        and not labels[-1].isdigit()
    )
