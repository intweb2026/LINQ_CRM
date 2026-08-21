from django.contrib import admin
from django.utils.html import format_html
from .models import GoogleSheetSource


@admin.register(GoogleSheetSource)
class GoogleSheetSourceAdmin(admin.ModelAdmin):
    list_display  = [
        "id", "name", "worksheet_name", "sheet_type", "status_badge",
        "records_count", "sync_frequency", "last_synced_at", "is_active", "created_at",
    ]
    list_filter   = ["is_active", "sync_enabled", "sheet_type", "sync_status", "sync_frequency"]
    search_fields = ["name", "worksheet_name", "sheet_id", "sheet_url", "notes"]
    readonly_fields = [
        "sync_status", "records_count", "last_synced_at",
        "last_successful_sync", "last_failed_sync", "last_error",
        "created_by", "created_at", "updated_at",
    ]
    fieldsets = (
        ("Identity", {
            "fields": ("name", "description", "sheet_type", "is_active"),
        }),
        ("Google Sheet Connection", {
            "fields": ("sheet_url", "sheet_id", "worksheet_name"),
        }),
        ("Sync Settings", {
            "fields": ("sync_enabled", "sync_frequency"),
        }),
        ("Configuration (JSON)", {
            "classes": ("collapse",),
            "fields": (
                "column_mappings", "transformation_config",
                "filter_config", "grouping_config", "formula_config",
            ),
        }),
        ("Sync Status (read-only)", {
            "fields": (
                "sync_status", "records_count",
                "last_synced_at", "last_successful_sync",
                "last_failed_sync", "last_error",
            ),
        }),
        ("Audit", {
            "fields": ("notes", "created_by", "created_at", "updated_at"),
        }),
    )
    ordering = ["-created_at"]

    def status_badge(self, obj):
        colours = {
            "never":   "#94a3b8",
            "idle":    "#64748b",
            "syncing": "#d97706",
            "success": "#16a34a",
            "partial": "#ea580c",
            "failed":  "#dc2626",
        }
        colour = colours.get(obj.sync_status, "#64748b")
        return format_html(
            '<span style="color:{};font-weight:600">{}</span>',
            colour, obj.get_sync_status_display(),
        )
    status_badge.short_description = "Sync Status"
