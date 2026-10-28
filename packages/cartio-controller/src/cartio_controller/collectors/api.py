"""The engineer-facing API for customers, engagements and sites.

Records are archived instead of deleted. Engagements and sites live under their customer's URL,
and every lookup is filtered by that customer, so a record can't be reached through another
customer's path.
"""

import uuid

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from . import audit, jobs
from .credentials import create_credential, delete_credential, update_credential
from .models import (
    AuditEvent,
    CollectionRun,
    Collector,
    Credential,
    Customer,
    Engagement,
    Job,
    Site,
)
from .permissions import JobPermissions, ViewModelPermissions
from .runs import RunNotAllowed
from .serializers import (
    AuditEventSerializer,
    CollectionRunSerializer,
    CredentialSerializer,
    CustomerSerializer,
    EngagementSerializer,
    JobSerializer,
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
        self._save(serializer, AuditEvent.Action.RECORD_CREATED)

    def perform_update(self, serializer):
        self.check_parent()
        if serializer.instance.is_archived:
            raise serializers.ValidationError("Restore this record before changing it.")
        self._save(
            serializer, AuditEvent.Action.RECORD_UPDATED, fields=sorted(serializer.validated_data)
        )

    def _audit(self, record, action, **detail) -> None:
        """Log a change to `record` in its customer's audit log, in the change's transaction."""
        audit.record(
            record if isinstance(record, Customer) else record.customer_id,
            action,
            actor=self.request.user.get_username(),
            object_type=record._meta.model_name,
            object_id=record.pk,
            object_name=str(getattr(record, "name", "")),
            **detail,
        )

    def _save(self, serializer, action, fields=None, **extra):
        # The serializers check names and dates first; this catches a concurrent conflict.
        try:
            with transaction.atomic():
                record = serializer.save(**extra)
                detail = {"fields": fields} if fields is not None else {}
                if isinstance(record, Customer):
                    detail["data_mode"] = record.data_mode
                    detail["live_view_allowed"] = record.live_view_allowed
                self._audit(record, action, **detail)
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
            with transaction.atomic():
                record.archived_at = timezone.now()
                record.save(update_fields=["archived_at"])
                self._audit(record, AuditEvent.Action.RECORD_ARCHIVED)
        return Response(self.get_serializer(record).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, *args, **kwargs):
        self.check_parent()
        record = self.get_object()
        if record.is_archived:
            with transaction.atomic():
                record.archived_at = None
                record.save(update_fields=["archived_at"])
                self._audit(record, AuditEvent.Action.RECORD_RESTORED)
        return Response(self.get_serializer(record).data)


class CustomerViewSet(ArchivableViewSet):
    serializer_class = CustomerSerializer

    def get_queryset(self):
        return self.filter_archived(Customer.objects.all())


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
        self._save(serializer, AuditEvent.Action.RECORD_CREATED, customer=self.get_customer())


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
    permission_classes = [IsAdminUser, ViewModelPermissions]

    def get_queryset(self):
        runs = self.scoped_queryset().select_related("result")
        params = self.request.query_params
        engagement = params.get("engagement")
        if engagement:
            engagement_id = serializers.IntegerField(min_value=1, max_value=2**63 - 1)
            try:
                runs = runs.filter(engagement_id=engagement_id.run_validation(engagement))
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({"engagement": "Give an engagement id."}) from exc
        collector = params.get("collector")
        if collector:
            try:
                runs = runs.filter(collector_id=uuid.UUID(collector))
            except ValueError as exc:
                raise serializers.ValidationError({"collector": "Give a collector id."}) from exc
        return runs


class JobViewSet(
    CustomerScopeMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Queue jobs for a customer's collectors, follow their state, cancel them, read results.

    Queuing writes the run's `run.started` event and cancelling a `job.cancelled` event.
    """

    model = Job
    serializer_class = JobSerializer
    permission_classes = [IsAdminUser, JobPermissions]

    def get_queryset(self):
        return self.scoped_queryset().select_related("collector", "engagement")

    def perform_create(self, serializer):
        customer = self.get_customer()
        data = serializer.validated_data
        collector = Collector.objects.for_customer(customer).filter(pk=data["collector_id"]).first()
        if collector is None:
            raise serializers.ValidationError({"collector": "No such collector for this customer."})
        engagement = None
        if data.get("engagement_id") is not None:
            engagement = (
                Engagement.objects.for_customer(customer).filter(pk=data["engagement_id"]).first()
            )
            if engagement is None:
                raise serializers.ValidationError(
                    {"engagement": "No such engagement for this customer."}
                )
        try:
            serializer.instance = jobs.queue_job(
                collector,
                data["type"],
                self.request.user.get_username(),
                engagement=engagement,
                params=data.get("params") or {},
                credential_ids=data.get("credentials") or [],
            )
        except (jobs.JobError, RunNotAllowed) as exc:
            raise serializers.ValidationError(str(exc)) from exc

    @action(detail=True, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        try:
            job = jobs.cancel_job(self.get_object(), request.user.get_username())
        except jobs.JobClosed as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=["get"])
    def result(self, request, *args, **kwargs):
        """The uploaded result, as the collector sent it."""
        job = self.get_object()
        if job.result_id is None:
            raise serializers.ValidationError("This job has no result yet.")
        return HttpResponse(job.result_bytes(), content_type="application/octet-stream")


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

    # A POST, since exporting writes an `audit.exported` event: a cross-site link can't add one.
    @action(detail=False, methods=["post"])
    def export(self, request, *args, **kwargs):
        """The whole log, oldest first, as a file: `?type=csv` or `?type=json` (the default)."""
        fmt = request.query_params.get("type", "json")
        if fmt not in ("csv", "json"):
            raise serializers.ValidationError({"type": "Export as csv or json."})
        customer = self.get_customer()
        chunks = audit.export_events(customer, request.user.get_username(), fmt)
        content_type = "text/csv" if fmt == "csv" else "application/json"
        response = StreamingHttpResponse(chunks, content_type=f"{content_type}; charset=utf-8")
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
