"""
webhooks/models.py
───────────────────
WebhookApiKey  — per-integration API keys with usage tracking
WebhookLog     — full lifecycle audit log for every inbound request
"""
import secrets
from django.conf import settings
from django.db import models
from django.utils import timezone


class WebhookApiKey(models.Model):
    """Per-integration API key stored in the database."""

    class Target(models.TextChoices):
        """
        Which ingest endpoint a key may post to.

        EMPTY IS THE ONLY SAFE DEFAULT AND MUST STAY THAT WAY. Every key issued
        before this field existed reads as empty, and empty means unrestricted,
        so those keys keep working on every endpoint exactly as they did. Any
        change that makes a blank target refuse a delivery breaks every live
        website integration at once.
        """
        BOOKINGS     = "bookings",     "Bookings"
        TICKETS      = "tickets",      "Tickets"
        PAPER_REVIEW = "paper_review", "Paper reviews"

    # url name per target, so the ONE place a path is written stays webhooks/urls.py
    # and both the API and the keys page read it from there through reverse().
    # A target missing from this map falls back to the booking URL rather than
    # raising, since a key is a credential and must not stop being listable
    # because someone added a choice and forgot a route.
    TARGET_URL_NAMES = {
        "":                   "webhook-ingest",
        Target.BOOKINGS:      "webhook-ingest",
        Target.TICKETS:       "webhook-ingest-tickets",
        Target.PAPER_REVIEW:  "webhook-ingest-paper-review",
    }

    name            = models.CharField(max_length=100)
    api_key         = models.CharField(max_length=80, unique=True, db_index=True)
    event           = models.CharField(max_length=50, blank=True, default="",
                                       help_text="Optional: restrict to this event code")
    target          = models.CharField(
        max_length=20, choices=Target.choices, blank=True, default="",
        help_text="Optional: restrict to one ingest endpoint; empty = every endpoint",
    )
    is_active       = models.BooleanField(default=True, db_index=True)
    allowed_domains = models.JSONField(default=list, blank=True,
                                       help_text="List of allowed origin domains; empty = unrestricted")
    notes           = models.TextField(blank=True, default="")

    created_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="created_webhook_keys",
    )
    created_at   = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
    usage_count  = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "webhook_api_keys"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({'active' if self.is_active else 'inactive'})"

    @staticmethod
    def generate_key() -> str:
        return "crm_live_" + secrets.token_urlsafe(36)

    def record_usage(self):
        self.last_used_at = timezone.now()
        self.usage_count  += 1
        self.save(update_fields=["last_used_at", "usage_count"])

    def ingest_path(self) -> str:
        """The path this key posts to, resolved from urls.py rather than typed."""
        from django.urls import reverse
        return reverse(
            self.TARGET_URL_NAMES.get(self.target) or "webhook-ingest"
        )

    def regenerate(self) -> str:
        self.api_key = WebhookApiKey.generate_key()
        self.save(update_fields=["api_key"])
        return self.api_key


class WebhookLog(models.Model):
    """Full lifecycle audit record for a single inbound webhook request."""

    class Status(models.TextChoices):
        RECEIVED   = "received",   "Received"
        PROCESSING = "processing", "Processing"
        SUCCESS    = "success",    "Success"
        FAILED     = "failed",     "Failed"
        DUPLICATE  = "duplicate",  "Duplicate"

    class ProcessingStatus(models.TextChoices):
        PENDING   = "pending",   "Pending"
        PROCESSED = "processed", "Processed"
        ERROR     = "error",     "Error"

    class DbInsertStatus(models.TextChoices):
        INSERTED  = "inserted",  "Inserted"
        UPDATED   = "updated",   "Updated"
        DUPLICATE = "duplicate", "Duplicate"
        FAILED    = "failed",    "Failed"
        PARTIAL   = "partial",   "Partial"

    # ── Auth ──────────────────────────────────────────────────────────────────
    api_key = models.ForeignKey(
        WebhookApiKey,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="logs",
    )

    # ── Request metadata ──────────────────────────────────────────────────────
    source         = models.CharField(max_length=100, blank=True, default="")
    ip_address     = models.GenericIPAddressField(null=True, blank=True)
    request_method = models.CharField(max_length=10, default="POST")

    # ── Payload ───────────────────────────────────────────────────────────────
    payload  = models.JSONField(default=dict)
    headers  = models.JSONField(default=dict)
    response = models.JSONField(default=dict)

    # ── Outcome ───────────────────────────────────────────────────────────────
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED, db_index=True)
    http_status = models.PositiveIntegerField(default=202)

    # ── Booking identifiers (denormalised for fast filtering) ─────────────────
    invoice_number = models.CharField(max_length=100, blank=True, default="", db_index=True)
    event_code     = models.CharField(max_length=50, blank=True, default="")
    event_name     = models.CharField(max_length=255, blank=True, default="")

    # ── Error detail ──────────────────────────────────────────────────────────
    error_message = models.TextField(blank=True, default="")
    stack_trace   = models.TextField(blank=True, default="")

    # ── Retry tracking ────────────────────────────────────────────────────────
    retry_count = models.PositiveIntegerField(default=0)

    # ── Processing state ──────────────────────────────────────────────────────
    processing_status = models.CharField(
        max_length=20, choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING, db_index=True,
    )
    processing_notes = models.TextField(blank=True, default="")

    # ── DB operation outcome ──────────────────────────────────────────────────
    db_insert_status = models.CharField(
        max_length=20, choices=DbInsertStatus.choices, blank=True, default="",
    )
    records_inserted = models.PositiveIntegerField(default=0)
    records_updated  = models.PositiveIntegerField(default=0)
    records_failed   = models.PositiveIntegerField(default=0)

    # ── Timing ────────────────────────────────────────────────────────────────
    received_at           = models.DateTimeField(null=True, blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processed_at          = models.DateTimeField(null=True, blank=True)
    processing_duration   = models.FloatField(null=True, blank=True)

    # ── Booking link ──────────────────────────────────────────────────────────
    created_booking = models.ForeignKey(
        "book_event.BookEvent",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="webhook_logs",
    )
    created_delegates_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "webhook_events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"],            name="wh_ev_status_idx"),
            models.Index(fields=["processing_status"], name="wh_ev_proc_idx"),
            models.Index(fields=["created_at"],        name="wh_ev_created_idx"),
            models.Index(fields=["invoice_number"],    name="wh_ev_invoice_idx"),
            # received_at is what the Webhook Logs table SORTS BY — it is that
            # column's default ordering in the UI, not created_at, which is the
            # one that had an index. Every visit therefore sorted all 130,304 rows
            # from scratch: measured at 416 ms for a single 50-row page, the
            # slowest query anywhere in the app. The column is nullable, and
            # PostgreSQL indexes nulls, so the DESC ordering uses this too.
            models.Index(fields=["received_at"],       name="wh_ev_received_idx"),
            # The status tabs and the dashboard's "failed deliveries" tile both ask
            # for one status ordered by received_at, which the two single-column
            # indexes above cannot answer together: PostgreSQL picks one, then sorts
            # or filters the rest by hand. Over 55,428 failed rows that was the
            # slowest request left in the app. DESC because that is the direction
            # every caller reads it in, newest first.
            models.Index(fields=["status", "-received_at"], name="wh_ev_status_recv_idx"),
            # created_at is the model's Meta.ordering, so it is what direct ORM
            # iteration and any caller that does not pass ?ordering= sorts by. The
            # single-column wh_ev_created_idx above leads correctly but leaves the
            # pk tiebreak to a sort step over 130,304 rows.
            models.Index(fields=["-created_at", "-id"], name="wh_ev_created_id_idx"),
        ]

    def __str__(self):
        return f"[{self.status}] {self.invoice_number or '—'} @ {self.created_at:%Y-%m-%d %H:%M}"
