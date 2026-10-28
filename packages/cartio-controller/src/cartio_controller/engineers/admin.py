from django.contrib import admin

from .models import EngineerMFA


@admin.register(EngineerMFA)
class EngineerMFAAdmin(admin.ModelAdmin):
    """Shows who has an authenticator. Replace one with `enroll_engineer --reset`."""

    list_display = ["user", "confirmed_at", "created_at"]
    fields = ["user", "confirmed_at", "created_at"]
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
