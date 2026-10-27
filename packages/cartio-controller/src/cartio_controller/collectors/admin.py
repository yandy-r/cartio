from django.contrib import admin

from .models import CheckIn, Collector, Customer


class CollectorInline(admin.TabularInline):
    model = Collector
    fields = ["name", "hostname", "version", "last_check_in", "enrolled_at"]
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["name", "created_at"]
    search_fields = ["name"]
    inlines = [CollectorInline]


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
