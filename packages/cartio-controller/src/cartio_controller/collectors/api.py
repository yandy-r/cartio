"""The engineer-facing API for customers, engagements and sites.

Records are archived instead of deleted. Engagements and sites live under their customer's URL,
and every lookup is filtered by that customer, so a record can't be reached through another
customer's path.
"""

import uuid

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from . import audit
from .credentials import create_credential, delete_credential, update_credential
from .models import AuditEvent, CollectionRun, Credential, Customer, Engagement, Site
from .permissions import ViewModelPermissions
from .serializers import (
    AuditEventSerializer,
    CollectionRunSerializer,
    CredentialSerializer,
    CustomerSerializer,
    EngagementSerializer,
    SiteSerializer,
)
from .vault import VaultError

_TRUE = {"1", "true", "yes"}


class ServiceUnavailable(APIException):
    status_code = 503
    default_detail = "The controller isn't set up for this yet."


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

    def perform_create(self, serializer):
        with transaction.atomic():
            self._save(serializer)
            customer = serializer.instance
            audit.record(
                customer,
                AuditEvent.Action.CUSTOMER_CREATED,
                actor=self.request.user.get_username(),
                object_type="customer",
                object_id=customer.pk,
                object_name=customer.name,
                data_mode=customer.data_mode,
            )


class CustomerScopeMixin:
    """Records always reached through, and filtered by, the customer in the URL."""

    model = None

    def get_customer(self) -> Customer:
        if not hasattr(self, "_customer"):
            self._customer = get_object_or_404(Customer, pk=self.kwargs["customer_pk"])
        return self._customer

    def scoped_queryset(self):
        return self.model.objects.for_customer(self.get_customer()).select_related("customer")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if "customer_pk" in self.kwargs:
            context["customer"] = self.get_customer()
        return context


class CustomerScopedViewSet(CustomerScopeMixin, ArchivableViewSet):
    """Engagements and sites: archived, never deleted."""

    def get_queryset(self):
        return self.filter_archived(self.scoped_queryset())

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


class CredentialViewSet(
    CustomerScopeMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Create, list, replace and delete credentials. Secrets go in and never come back out."""

    model = Credential
    serializer_class = CredentialSerializer

    def get_queryset(self):
        return self.scoped_queryset()

    def _write(self, action):
        try:
            with transaction.atomic():
                return action()
        except ImproperlyConfigured as exc:
            raise ServiceUnavailable(str(exc)) from exc
        except VaultError as exc:
            raise ServiceUnavailable(
                "Stored credentials can't be decrypted with the configured master key."
            ) from exc
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise serializers.ValidationError({"name": "This name is already taken."}) from exc
            raise

    def perform_create(self, serializer):
        customer = self.get_customer()
        if customer.is_archived:
            raise serializers.ValidationError("This customer is archived. Restore it first.")
        data = dict(serializer.validated_data)
        secret = data.pop("secret")
        serializer.instance = self._write(
            lambda: create_credential(customer, self.request.user.get_username(), secret, **data)
        )

    def perform_update(self, serializer):
        data = dict(serializer.validated_data)
        secret = data.pop("secret", None)
        serializer.instance = self._write(
            lambda: update_credential(
                serializer.instance, self.request.user.get_username(), secret, **data
            )
        )

    def perform_destroy(self, instance):
        self._write(lambda: delete_credential(instance, self.request.user.get_username()))


class CollectionRunViewSet(
    CustomerScopeMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """A customer's collection runs, newest first. Filter with `?engagement=` and `?collector=`.

    Read-only: runs are recorded by the job channel, never written through the API.
    """

    model = CollectionRun
    serializer_class = CollectionRunSerializer

    def get_queryset(self):
        runs = self.scoped_queryset().select_related("result")
        params = self.request.query_params
        engagement = params.get("engagement")
        if engagement:
            if not engagement.isdigit():
                raise serializers.ValidationError({"engagement": "Give an engagement id."})
            runs = runs.filter(engagement_id=int(engagement))
        collector = params.get("collector")
        if collector:
            try:
                runs = runs.filter(collector_id=uuid.UUID(collector))
            except ValueError as exc:
                raise serializers.ValidationError({"collector": "Give a collector id."}) from exc
        return runs


class AuditEventViewSet(
    CustomerScopeMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """A customer's audit log, newest first, and its export for compliance requests.

    Reading it needs the audit event view permission, not just a staff account.
    """

    model = AuditEvent
    serializer_class = AuditEventSerializer
    permission_classes = [IsAdminUser, ViewModelPermissions]

    def get_queryset(self):
        return self.scoped_queryset()

    @action(detail=False, methods=["get"])
    def export(self, request, *args, **kwargs):
        """The whole log, oldest first, as a file: `?type=csv` or `?type=json` (the default)."""
        fmt = request.query_params.get("type", "json")
        if fmt not in ("csv", "json"):
            raise serializers.ValidationError({"type": "Export as csv or json."})
        customer = self.get_customer()
        with transaction.atomic():
            body = audit.export_events(customer, request.user.get_username(), fmt)
        content_type = "text/csv" if fmt == "csv" else "application/json"
        response = HttpResponse(body, content_type=f"{content_type}; charset=utf-8")
        filename = f"cartio-audit-customer-{customer.pk}.{fmt}"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


def _is_unique_violation(exc: IntegrityError) -> bool:
    cause = exc.__cause__
    sqlstate = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    if sqlstate is not None:
        return sqlstate == "23505"
    # SQLite, used only in local development, has no SQLSTATE.
    return "UNIQUE" in str(exc)
