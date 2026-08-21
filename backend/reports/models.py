"""
reports/models.py
──────────────────
GoogleSheetSource — configures one Google Sheet tab as a CRM data source.

The only model left in this app. ReportDefinition, ReportRow and ReportSyncLog
went with the Reports page: they existed to hold the rows that page previewed and
the per-run log its Sync Logs tab listed, and nothing reads either now. What
remains is the registry behind the Google Sync page's "Add sheet source" — a
stored connection plus the live worksheet lookup that fills it in.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class GoogleSheetSource(models.Model):
    """Represents a single Google Sheet worksheet configured as a data source."""

    class SheetType(models.TextChoices):
        BOOKINGS   = "bookings",   "Bookings"
        EVENTS     = "events",     "Events"
        DELEGATES  = "delegates",  "Delegates"
        REVENUE    = "revenue",    "Revenue"
        PIPELINE   = "pipeline",   "Pipeline"
        ATTENDANCE = "attendance", "Attendance"
        CUSTOM     = "custom",     "Custom"

    class SyncStatus(models.TextChoices):
        NEVER    = "never",    "Never Synced"
        IDLE     = "idle",     "Idle"
        SYNCING  = "syncing",  "Syncing"
        SUCCESS  = "success",  "Success"
        FAILED   = "failed",   "Failed"
        PARTIAL  = "partial",  "Partial"

    class SyncFrequency(models.TextChoices):
        MANUAL  = "manual",  "Manual Only"
        HOURLY  = "hourly",  "Every Hour"
        DAILY   = "daily",   "Daily"
        WEEKLY  = "weekly",  "Weekly"

    # ── Identity ──────────────────────────────────────────────────────────────
    name             = models.CharField(max_length=200)
    description      = models.TextField(blank=True, default="")
    sheet_id         = models.CharField(
        max_length=200,
        help_text="Google Sheet ID extracted from URL, or the full URL itself",
    )
    sheet_url        = models.URLField(blank=True, default="",
                                       help_text="Full Google Sheets URL (optional, for reference)")
    worksheet_name   = models.CharField(max_length=200, default="Sheet1",
                                        help_text="Exact tab/worksheet name to read")
    sheet_type       = models.CharField(max_length=20, choices=SheetType.choices,
                                        default=SheetType.CUSTOM)

    # ── Control flags ─────────────────────────────────────────────────────────
    is_active        = models.BooleanField(default=True, db_index=True)
    sync_enabled     = models.BooleanField(default=True)
    sync_frequency   = models.CharField(max_length=20, choices=SyncFrequency.choices,
                                        default=SyncFrequency.MANUAL)

    # ── Column & processing configuration (JSON) ──────────────────────────────
    column_mappings       = models.JSONField(
        default=dict, blank=True,
        help_text='Map sheet column headers to CRM field names. e.g. {"Invoice No": "invoice_number"}',
    )
    transformation_config = models.JSONField(
        default=dict, blank=True,
        help_text='Per-column transformation rules. e.g. {"Date": ["date_iso"], "Amount": ["strip_currency"]}',
    )
    filter_config    = models.JSONField(default=dict, blank=True,
                                        help_text="Default filter rules for this source")
    grouping_config  = models.JSONField(default=dict, blank=True,
                                        help_text="Column grouping and aggregation config")
    formula_config   = models.JSONField(default=dict, blank=True,
                                        help_text="Formula definitions reproduced from Google Sheets")

    # ── Sync tracking ─────────────────────────────────────────────────────────
    # Left on the model, no longer written by anything: the importer that set
    # them is gone with the Reports page. Kept so a source registered before the
    # removal still reads back its last known state.
    last_synced_at        = models.DateTimeField(null=True, blank=True)
    last_successful_sync  = models.DateTimeField(null=True, blank=True)
    last_failed_sync      = models.DateTimeField(null=True, blank=True)
    sync_status           = models.CharField(max_length=20, choices=SyncStatus.choices,
                                             default=SyncStatus.NEVER, db_index=True)
    records_count         = models.PositiveIntegerField(default=0)
    last_error            = models.TextField(blank=True, default="")

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="created_sheet_sources",
    )
    created_at  = models.DateTimeField(default=timezone.now)
    updated_at  = models.DateTimeField(auto_now=True)
    notes       = models.TextField(blank=True, default="")

    class Meta:
        db_table = "report_sheet_sources"
        ordering = ["-created_at"]
        verbose_name = "Google Sheet Source"
        verbose_name_plural = "Google Sheet Sources"

    def __str__(self):
        return f"{self.name} ({self.worksheet_name})"

    @staticmethod
    def extract_sheet_id(url_or_id: str) -> str:
        """Parse a Google Sheets URL and return just the spreadsheet ID."""
        if "/" in url_or_id:
            parts = url_or_id.split("/")
            for i, part in enumerate(parts):
                if part == "d" and i + 1 < len(parts):
                    candidate = parts[i + 1]
                    # Remove any query string or hash
                    return candidate.split("?")[0].split("#")[0]
        return url_or_id.strip()
