from django.contrib import admin
from django.utils.html import format_html

from .models import DataApiKey


@admin.register(DataApiKey)
class DataApiKeyAdmin(admin.ModelAdmin):
    list_display = [
        "id", "name", "key_preview_display", "active_badge",
        "scopes_display", "usage_count", "last_used_at", "expires_at", "created_at",
    ]
    list_filter = ["is_active"]
    search_fields = ["name", "notes"]
    readonly_fields = ["key_hash", "key_preview", "created_at", "last_used_at", "usage_count"]
    ordering = ["-created_at"]

    def key_preview_display(self, obj):
        return format_html('<code style="font-size:11px">{}</code>', obj.key_preview)
    key_preview_display.short_description = "Key Preview"

    def active_badge(self, obj):
        colour = "#16a34a" if obj.is_active else "#dc2626"
        label = "Active" if obj.is_active else "Disabled"
        return format_html('<span style="color:{};font-weight:600">{}</span>', colour, label)
    active_badge.short_description = "Status"

    def scopes_display(self, obj):
        if not obj.scopes:
            return "All"
        return ", ".join(obj.scopes)
    scopes_display.short_description = "Scopes"

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
