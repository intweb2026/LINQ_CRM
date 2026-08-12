"""
google_sync/models.py
──────────────────────
Per-run audit log for every Google Sheets sync operation.
One row = one sync run (or one sheet within a full sync).
"""
from django.db import models
from django.utils import timezone


class GoogleSheetSyncLog(models.Model):

    class SyncType(models.TextChoices):
        BOOKINGS   = "bookings",   "Bookings"
        EVENTS     = "events",     "Events"
        FULL_SYNC  = "full_sync",  "Full Sync"
        CRM_MIRROR = "crm_mirror", "CRM Mirror"

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
