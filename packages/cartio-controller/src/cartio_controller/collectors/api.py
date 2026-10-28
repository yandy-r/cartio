"""The engineer-facing API for customers, engagements and sites.

Records are archived instead of deleted. Engagements and sites live under their customer's URL,
and every lookup is filtered by that customer, so a record can't be reached through another
customer's path.
"""

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Customer, Engagement, Site
from .serializers import CustomerSerializer, EngagementSerializer, SiteSerializer

_TRUE = {"1", "true", "yes"}


class ArchivableViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Create, list, read, update, archive and restore. No delete."""

    def filter_archived(self, queryset):
        if self.action == "list" and self.request.query_params.get("archived", "") not in _TRUE:
            return queryset.filter(archived_at__isnull=True)
        return queryset

    def perform_create(self, serializer):
        self._save(serializer)

    def perform_update(self, serializer):
        self.check_parent()
        if serializer.instance.is_archived:
            raise serializers.ValidationError("Restore this record before changing it.")
        self._save(serializer)

    def _save(self, serializer, **extra):
        # The serializers check names and dates first; this catches a concurrent conflict.
        try:
            with transaction.atomic():
                serializer.save(**extra)
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise serializers.ValidationError({"name": "This name is already taken."}) from exc
            raise serializers.ValidationError("The change conflicts with another one.") from exc

    def check_parent(self) -> None:
        """Refuse changes when the record's parent is archived. Customers have no parent."""

    @action(detail=True, methods=["post"])
    def archive(self, request, *args, **kwargs):
        self.check_parent()
        record = self.get_object()
        if not record.is_archived:
            record.archived_at = timezone.now()
            record.save(update_fields=["archived_at"])
        return Response(self.get_serializer(record).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, *args, **kwargs):
        self.check_parent()
        record = self.get_object()
        if record.is_archived:
            record.archived_at = None
            record.save(update_fields=["archived_at"])
        return Response(self.get_serializer(record).data)


class CustomerViewSet(ArchivableViewSet):
    serializer_class = CustomerSerializer

    def get_queryset(self):
        return self.filter_archived(Customer.objects.all())


class CustomerScopedViewSet(ArchivableViewSet):
    """Engagements and sites: always reached through, and filtered by, one customer."""

    model = None

    def get_customer(self) -> Customer:
        if not hasattr(self, "_customer"):
            self._customer = get_object_or_404(Customer, pk=self.kwargs["customer_pk"])
        return self._customer

    def get_queryset(self):
        queryset = self.model.objects.filter(customer=self.get_customer())
        return self.filter_archived(queryset.select_related("customer"))

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if "customer_pk" in self.kwargs:
            context["customer"] = self.get_customer()
        return context

    def check_parent(self) -> None:
        if self.get_customer().is_archived:
            raise serializers.ValidationError("This customer is archived. Restore it first.")

    def perform_create(self, serializer):
        self.check_parent()
        self._save(serializer, customer=self.get_customer())


class EngagementViewSet(CustomerScopedViewSet):
    model = Engagement
    serializer_class = EngagementSerializer


class SiteViewSet(CustomerScopedViewSet):
    model = Site
    serializer_class = SiteSerializer


def _is_unique_violation(exc: IntegrityError) -> bool:
    cause = exc.__cause__
    sqlstate = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    if sqlstate is not None:
        return sqlstate == "23505"
    # SQLite, used only in local development, has no SQLSTATE.
    return "UNIQUE" in str(exc)
