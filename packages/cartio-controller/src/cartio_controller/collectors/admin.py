from django.contrib import admin

from .models import AuditEvent, CheckIn, Collector, Credential, Customer, Engagement, Site


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
    list_display = ["name", "data_mode", "created_at", "archived_at"]
    list_filter = ["data_mode"]
    search_fields = ["name"]
    inlines = [CollectorInline]

    def get_readonly_fields(self, request, obj=None):
        # ADR-0002: data mode is chosen at creation; changing it needs its own audited action.
        return ["data_mode"] if obj is not None else []


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
    list_display = ["name", "customer", "hostname", "version", "last_check_in", "enrolled_at"]
    list_filter = ["customer"]
    search_fields = ["name", "hostname"]
    readonly_fields = [
        "id",
        "enrolled_at",
        "hostname",
        "version",
        "platform",
        "last_check_in",
    ]
    exclude = ["enrollment_token_hash", "secret_hash"]

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
