from django.contrib import admin

from .models import (
    AuditEvent,
    CheckIn,
    CollectionRun,
    CollectionRunResult,
    Collector,
    Credential,
    Customer,
    Engagement,
    Job,
    LoginEvent,
    Site,
)


class CollectorInline(admin.TabularInline):
    model = Collector
    fields = ["name", "hostname", "version", "last_check_in", "enrolled_at"]
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True


class ArchiveNotDeleteAdmin(admin.ModelAdmin):
    """Customers, engagements and sites are archived, never deleted."""

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Customer)
class CustomerAdmin(ArchiveNotDeleteAdmin):
    list_display = [
        "name",
        "data_mode",
        "live_view_allowed",
        "require_uninspected_path",
        "created_at",
        "archived_at",
    ]
    list_filter = ["data_mode", "live_view_allowed", "require_uninspected_path"]
    search_fields = ["name"]
    inlines = [CollectorInline]

    def get_readonly_fields(self, request, obj=None):
        # ADR-0002 and ADR-0005: data mode and live view are chosen at creation; changing
        # either needs its own audited action.
        return ["data_mode", "live_view_allowed"] if obj is not None else []


@admin.register(Engagement)
class EngagementAdmin(ArchiveNotDeleteAdmin):
    list_display = ["name", "customer", "status", "start_date", "end_date", "archived_at"]
    list_filter = ["customer", "status"]
    search_fields = ["name"]

    def get_readonly_fields(self, request, obj=None):
        return ["customer"] if obj is not None else []


@admin.register(Site)
class SiteAdmin(ArchiveNotDeleteAdmin):
    list_display = ["name", "customer", "location", "archived_at"]
    list_filter = ["customer"]
    search_fields = ["name", "location"]

    def get_readonly_fields(self, request, obj=None):
        return ["customer"] if obj is not None else []


@admin.register(Collector)
class CollectorAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "customer",
        "hostname",
        "version",
        "last_check_in",
        "enrolled_at",
        "revoked_at",
    ]
    list_filter = ["customer"]
    search_fields = ["name", "hostname"]
    readonly_fields = [
        "id",
        "enrolled_at",
        # Revoking is the engineer UI's audited action (YAN-1736), not a field edit.
        "revoked_at",
        "hostname",
        "version",
        "platform",
        "last_check_in",
        "tls_inspected",
        # Set by the collector at enrollment; changing it would hand the identity to another key.
        "public_key",
        # Also set at enrollment: job credentials are sealed to it.
        "seal_key",
        "enrollment_expires_at",
    ]
    exclude = ["enrollment_token_hash"]

    def get_readonly_fields(self, request, obj=None):
        # A collector belongs to one customer for good (YAN-1649).
        return [*self.readonly_fields, "customer"] if obj is not None else self.readonly_fields


@admin.register(CheckIn)
class CheckInAdmin(admin.ModelAdmin):
    list_display = ["collector", "at", "hostname", "version", "platform"]
    list_filter = ["collector__customer"]
    readonly_fields = ["customer", "collector", "at", "hostname", "version", "platform"]


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Credential)
class CredentialAdmin(ReadOnlyAdmin):
    """Credentials are managed through the API, which encrypts and audits. Secrets never show."""

    list_display = ["name", "customer", "kind", "username", "updated_at"]
    list_filter = ["customer", "kind"]
    search_fields = ["name", "username"]
    exclude = ["ciphertext"]


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
    list_display = ["at", "customer", "action", "object_name", "actor", "collector"]
    list_filter = ["customer", "action"]
    search_fields = ["object_name", "actor"]


class CollectionRunResultInline(admin.StackedInline):
    model = CollectionRunResult
    fields = ["outcome", "finished_at", "device_count", "error_summary"]
    readonly_fields = fields
    extra = 0
    max_num = 0
    can_delete = False

    # Results are written only by runs.finish_run, which also logs them.
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CollectionRun)
class CollectionRunAdmin(ReadOnlyAdmin):
    """Runs are recorded by the job channel. Engineers filter them here (YAN-1654)."""

    list_display = [
        "started_at",
        "customer",
        "engagement",
        "collector",
        "job_type",
        "started_by",
        "outcome",
        "device_count",
    ]
    list_filter = ["customer", "engagement", "collector", "job_type", "result__outcome"]
    list_select_related = ["customer", "engagement", "collector", "result"]
    search_fields = ["job_type", "started_by"]
    date_hierarchy = "started_at"
    inlines = [CollectionRunResultInline]

    @admin.display(description="Outcome")
    def outcome(self, run):
        result = getattr(run, "result", None)
        return result.get_outcome_display() if result else "Running"

    @admin.display(description="Devices")
    def device_count(self, run):
        result = getattr(run, "result", None)
        return result.device_count if result else None


@admin.register(Job)
class JobAdmin(ReadOnlyAdmin):
    """Jobs are queued and cancelled through the API and the Jobs screen, which audit."""

    list_display = ["created_at", "customer", "type", "collector", "status", "created_by"]
    list_filter = ["customer", "type", "status"]
    list_select_related = ["customer", "collector"]
    exclude = ["credential_ids"]


@admin.register(LoginEvent)
class LoginEventAdmin(ReadOnlyAdmin):
    list_display = ["at", "kind", "username", "ip_address"]
    list_filter = ["kind"]
    search_fields = ["username", "ip_address"]
    date_hierarchy = "at"
