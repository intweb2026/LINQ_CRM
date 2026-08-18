"""
google_sync/models.py
──────────────────────
Per-run audit log for every Google Sheets sync operation.
One row = one sync run (or one sheet within a full sync).
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class GoogleSheetSyncLog(models.Model):

    class SyncType(models.TextChoices):
        BOOKINGS     = "bookings",     "Bookings"
        EVENTS       = "events",       "Events"
        FULL_SYNC    = "full_sync",    "Full Sync"
        CRM_MIRROR   = "crm_mirror",   "CRM Mirror"
        SHEET_TARGET = "sheet_target", "Sheet Target"

    class Status(models.TextChoices):
        PENDING        = "pending",        "Pending"
        RUNNING        = "running",        "Running"
        SUCCESS        = "success",        "Success"
        FAILED         = "failed",         "Failed"
        PARTIAL        = "partial_success", "Partial Success"

    class SyncMode(models.TextChoices):
        INCREMENTAL = "incremental", "Incremental"
        FULL        = "full",        "Full"

    class TriggerSource(models.TextChoices):
        SCHEDULER    = "scheduler",    "Scheduler"
        ADMIN_MANUAL = "admin_manual", "Admin Manual"
        SYSTEM       = "system",       "System"

    # ── Identity ──────────────────────────────────────────────────────────────
    sync_type  = models.CharField(max_length=20, choices=SyncType.choices, db_index=True)
    sheet_name = models.CharField(max_length=200, blank=True, default="")

    # ── State ─────────────────────────────────────────────────────────────────
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)

    # ── Timing ────────────────────────────────────────────────────────────────
    started_at       = models.DateTimeField(default=timezone.now, db_index=True)
    completed_at     = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    # ── Counters ──────────────────────────────────────────────────────────────
    records_processed = models.PositiveIntegerField(default=0)
    records_created   = models.PositiveIntegerField(default=0)
    records_updated   = models.PositiveIntegerField(default=0)
    records_failed    = models.PositiveIntegerField(default=0)

    # ── Sync configuration ────────────────────────────────────────────────────
    sync_mode = models.CharField(max_length=20, choices=SyncMode.choices, default=SyncMode.INCREMENTAL)

    # ── Audit ─────────────────────────────────────────────────────────────────
    triggered_by   = models.CharField(max_length=150, blank=True, default="")
    trigger_source = models.CharField(max_length=20, choices=TriggerSource.choices, default=TriggerSource.SYSTEM)

    # ── Result detail ─────────────────────────────────────────────────────────
    error_message = models.TextField(blank=True, default="")
    sync_summary  = models.JSONField(default=dict, blank=True)

    # ── Incremental checkpoint ────────────────────────────────────────────────
    last_synced_record_id = models.BigIntegerField(null=True, blank=True)
    last_synced_at        = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "google_sync_logs"
        ordering = ["-started_at"]
        indexes  = [
            models.Index(fields=["status"],     name="gs_logs_status_idx"),
            models.Index(fields=["sync_type"],  name="gs_logs_type_idx"),
            models.Index(fields=["started_at"], name="gs_logs_started_idx"),
        ]

    def __str__(self):
        return f"[{self.sync_type}/{self.status}] {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def duration_display(self):
        if self.duration_seconds is None:
            return "—"
        if self.duration_seconds < 60:
            return f"{self.duration_seconds:.1f}s"
        return f"{self.duration_seconds / 60:.1f}m"


class SheetSyncTarget(models.Model):
    """
    One user-defined push: this module's columns, into that tab of that sheet.

    Where CRM_MODULES in sync/crm_mirror.py is the fixed nightly mirror of the
    whole CRM, a target is the narrow case — somebody wants three columns of
    bookings in a spreadsheet of their own, and wants to say so from the Google
    Sync page rather than by having the code changed.

    A run is a full replace of the tab, which is why (spreadsheet_id, tab_name)
    is unique: two targets writing one tab would each wipe the other's rows on
    alternate runs, and the tab would show whichever ran last with no sign that
    anything was lost.
    """

    class Status(models.TextChoices):
        NEVER   = "never",   "Never run"
        SUCCESS = "success", "Success"
        FAILED  = "failed",  "Failed"

    name = models.CharField(max_length=200, help_text="What this push is for.")

    # ── Destination ───────────────────────────────────────────────────────────
    spreadsheet_id = models.CharField(
        max_length=200,
        help_text="Google spreadsheet id. A pasted sheet URL is reduced to its id on save.",
    )
    tab_name = models.CharField(
        max_length=200,
        help_text="Tab to write. Created on the first run if it does not exist.",
    )

    # ── Selection ─────────────────────────────────────────────────────────────
    module = models.CharField(
        max_length=50, db_index=True,
        help_text="Catalogue key, see sync/catalog.py. e.g. 'bookings'.",
    )
    columns = models.JSONField(
        default=list,
        help_text='Column keys, in sheet order. e.g. ["delegate_name", "delegate_email"].',
    )

    is_enabled = models.BooleanField(default=True, db_index=True)

    # ── Last run ──────────────────────────────────────────────────────────────
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_status    = models.CharField(max_length=20, choices=Status.choices,
                                      default=Status.NEVER, db_index=True)
    last_error     = models.TextField(blank=True, default="")
    records_synced = models.PositiveIntegerField(default=0)

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="sheet_sync_targets",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "google_sheet_sync_targets"
        ordering = ["-created_at"]
        verbose_name = "Sheet sync target"
        verbose_name_plural = "Sheet sync targets"
        constraints = [
            models.UniqueConstraint(
                fields=["spreadsheet_id", "tab_name"],
                name="one_target_per_tab",
            ),
        ]
        indexes = [
            models.Index(fields=["module"], name="gs_target_module_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.module} → {self.tab_name})"
