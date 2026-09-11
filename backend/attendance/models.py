"""
attendance/models.py
─────────────────────
ONE table, and one row per person who walked through the door.

WHY THERE IS NO attendee_type/attendee_id PAIR
The brief described a generic (type, id) pair, for a CRM where Speakers and
Delegates are two tables. In THIS one they are not: a speaker is a
book_delegates row whose booking_code carries a speaker marker, which is how
pre_event_docs/services.role_of already tells the desk who is going on stage.
So the link is a real ForeignKey and the type is a derived LABEL, snapshotted
beside the name for the log's own columns. A generic pair here would be a
nullable id with no referential integrity, pointing at the one table an FK
describes exactly.

WHY THE NAME, EMAIL AND COMPANY ARE COPIED
They are the BADGE AS PRESENTED, the same reasoning as
pre_event_docs.BadgeIssue: the FK is SET_NULL, and an arrival log still has to
name who arrived after the booking row is transferred, renamed or deleted. It
also means reading the log is one table and no join.

THE UNIQUE CONSTRAINT IS THE DUPLICATE PREVENTION, not the view's SELECT.
Two turnstiles scanning one badge in the same instant both pass a "have we seen
this person" query and both go on to insert. The database is the only thing that
can arbitrate that, so the loser gets an IntegrityError and reports
already_checked_in; see views.scan.

Unique on `delegate` ALONE, which is the honest translation of the brief's
(event_code, attendee_type, attendee_id). A book_delegates row belongs to
exactly one event and carries exactly one booking code, so the event and the
type are functions of the delegate rather than independent parts of a key.
The same PERSON booked onto a second event is a second delegate row, so a
second check-in there is still allowed, which is the behaviour the brief asked
for.

WHY event_code AND edition ARE BOTH HERE
Because a delegate-side event code is not unique on its own. BookDelegate.save()
strips the trailing year out of event_code into `edition`, so two editions of a
recurring event both store "ACU" and are told apart by 2025 against 2026. A log
scoped or filtered on the code alone would merge them. See
pre_event_docs/services.py, which carries the same pair for the same reason.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class AttendanceRecord(models.Model):
    class Type(models.TextChoices):
        DELEGATE = "delegate", "Delegate"
        SPEAKER  = "speaker",  "Speaker"

    class Source(models.TextChoices):
        QR_SCAN = "qr_scan", "QR scan"
        MANUAL  = "manual",  "Manual"

    CHECKED_IN = "Checked-in"

    delegate = models.ForeignKey(
        "book_delegate.BookDelegate",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="attendance_records",
    )
    # Held alongside the FK, not read through it; see the module docstring.
    event_code = models.CharField(max_length=50, db_index=True)
    edition    = models.IntegerField(null=True, blank=True, db_index=True)

    attendee_type  = models.CharField(
        max_length=16, choices=Type.choices, default=Type.DELEGATE, db_index=True,
    )
    # THE BADGE SNAPSHOT. Never updated after the arrival that wrote it.
    attendee_name  = models.CharField(max_length=255)
    # CharField, not EmailField: this is a copy of whatever the booking held, and
    # an arrival must not be refusable because somebody's stored address is
    # malformed.
    attendee_email = models.CharField(max_length=254, blank=True, default="")
    company_name   = models.CharField(max_length=255, blank=True, default="")

    status = models.CharField(max_length=20, default=CHECKED_IN)

    # default=timezone.now rather than auto_now_add, deliberately. auto_now_add
    # sets editable=False, which hides the column from ModelSerializer and from
    # the admin, so the one fact the log exists to record would not be in its own
    # API response.
    checked_in_at = models.DateTimeField(default=timezone.now, db_index=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="attendance_checkins",
    )
    source = models.CharField(
        max_length=16, choices=Source.choices, default=Source.QR_SCAN,
    )

    class Meta:
        db_table = "attendance_records"
        ordering = ["-checked_in_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["delegate"], name="attendance_one_row_per_delegate",
            ),
        ]
        indexes = [
            # The log read: one event's arrivals, newest first.
            models.Index(
                "event_code", models.F("checked_in_at").desc(),
                name="att_event_time_idx",
            ),
        ]

    def __str__(self):
        return f"{self.attendee_name} {self.event_code} {self.checked_in_at:%Y-%m-%d %H:%M}"


class AttendanceQrEmailLog(models.Model):
    """
    One row per QR-code email attempt, "sent" or "failed". WHY BOTH STATUSES
    ARE LOGGED, not just successes: a "failed" row lets a re-run of
    send_qr_emails retry that person without a human first figuring out who
    was skipped, while the conditional unique constraint below still stops a
    SECOND successful send once one has already gone out.
    """
    class Status(models.TextChoices):
        SENT    = "sent",    "Sent"
        FAILED  = "failed",  "Failed"
        SKIPPED = "skipped", "Skipped"

    delegate = models.ForeignKey(
        "book_delegate.BookDelegate",
        on_delete=models.CASCADE,
        related_name="qr_email_logs",
    )
    event_code = models.CharField(max_length=50, db_index=True)
    edition    = models.IntegerField(null=True, blank=True, db_index=True)
    recipient_email = models.EmailField()
    status = models.CharField(max_length=16, choices=Status.choices)
    error_message = models.TextField(blank=True, default="")
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="qr_email_sends",
    )
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "attendance_qr_email_log"
        constraints = [
            # One SUCCESSFUL send per delegate, ever. A prior "failed" row does
            # not block a retry, because it never satisfies status="sent".
            models.UniqueConstraint(
                fields=["delegate"],
                condition=models.Q(status="sent"),
                name="attendance_qr_email_one_sent_per_delegate",
            ),
        ]

    def __str__(self):
        return f"{self.recipient_email} {self.event_code} {self.status}"
