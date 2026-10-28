from django.contrib import admin

from .models import CheckIn, Collector, Customer, Engagement, Site


class CollectorInline(admin.TabularInline):
    model = Collector
    fields = ["name", "hostname", "version", "last_check_in", "enrolled_at"]
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["name", "data_mode", "created_at", "archived_at"]
    list_filter = ["data_mode"]
    search_fields = ["name"]
    inlines = [CollectorInline]

    def get_readonly_fields(self, request, obj=None):
        # ADR-0002: data mode is chosen at creation; changing it needs its own audited action.
        return ["data_mode"] if obj is not None else []


@admin.register(Engagement)
class EngagementAdmin(admin.ModelAdmin):
    list_display = ["name", "customer", "status", "start_date", "end_date", "archived_at"]
    list_filter = ["customer", "status"]
    search_fields = ["name"]

    def get_readonly_fields(self, request, obj=None):
        return ["customer"] if obj is not None else []


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
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


@admin.register(CheckIn)
class CheckInAdmin(admin.ModelAdmin):
    list_display = ["collector", "at", "hostname", "version", "platform"]
    list_filter = ["collector__customer"]
    readonly_fields = ["collector", "at", "hostname", "version", "platform"]
