"""
paper_review/webhook.py
────────────────────────
PaperReviewProcessor — the paper-review half of POST /api/webhooks/paper-review/.

A DROP-IN FOR WebhookProcessor. Same __init__(log), same (success, result)
contract, same log-field responsibilities, so PaperReviewIngestionView is
WebhookIngestionView with one attribute changed: authentication, the
unparseable-body row, the crash handler and the response shaping are inherited,
not copied. Two ingestion endpoints that log differently is the failure this
avoids.

WHAT IT ACCEPTS
One JSON object per review, or {"rows": [...]} for a batch (MAX_ROWS per
delivery). Keys may be model field names OR the Zoho display labels — the same
header table the spreadsheet import uses, so whatever the sender already exports
lands without a translation step. Unrecognised keys are reported in the response,
never silently dropped.

IMPORT SEMANTICS, NOT FORM SEMANTICS — the one behaviour worth knowing before
pointing a sender at this. Rows are written exactly the way import/commit/ writes
them: NO ProposalSubmission is minted and NO production-team email is sent. Both
fire on a form create, where a person chose to create that one review; a sender
replaying a backlog, or retrying a delivery, must not mint proposals or send mail
per row. Structural, like paper_review/views.py's B2: there is no call path from
here to either workflow.

DUPLICATES WARN, THEY DO NOT BLOCK. Same rule as the importer: a repeat of
(email, event code) is written and flagged in the row's `warning`. The table
already treats duplicates as data to be reviewed (duplicate_count is a column),
and a webhook that silently dropped a re-send would hide a sender bug rather than
show it.
"""
import time
from types import SimpleNamespace

from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone

from accounts.import_common import as_text, normalise_row
from webhooks.models import WebhookLog
from webhooks.utils import unwrap_payload

from .importer import (
    ERROR, MAX_ROWS, classify_rows, map_headers, public_plan, summarise,
)
from .models import PaperReview

# classify_rows scopes event codes to the caller's own assignments, and a webhook
# has no user. An API key is issued by an admin from the keys page, so ingestion
# runs with the full visibility an admin import would have. has_full_visibility()
# reads exactly these two attributes and returns True on the second, so nothing
# further down reaches for a User the request does not have.
WEBHOOK_USER = SimpleNamespace(is_authenticated=True, is_admin=True)


class PaperReviewProcessor:

    def __init__(self, log: WebhookLog):
        self.log   = log
        self.notes = []

    def _note(self, msg: str):
        self.notes.append(f"[{timezone.now():%H:%M:%S}] {msg}")

    def _finish(self, *, started, http_status, error=""):
        log = self.log
        log.processing_duration = round(time.monotonic() - started, 3)
        log.processing_notes    = "\n".join(self.notes)
        log.processed_at        = timezone.now()
        log.http_status         = http_status
        log.error_message       = error
        log.save()

    def _fail(self, message, started, http_status=400):
        log = self.log
        log.status            = WebhookLog.Status.FAILED
        log.processing_status = WebhookLog.ProcessingStatus.ERROR
        log.db_insert_status  = WebhookLog.DbInsertStatus.FAILED
        self._note(f"FAILED: {message}")
        self._finish(started=started, http_status=http_status, error=message)
        return False, {"detail": message}

    def process(self) -> tuple[bool, dict]:
        log     = self.log
        started = time.monotonic()

        log.status                = WebhookLog.Status.PROCESSING
        log.processing_started_at = timezone.now()
        log.save(update_fields=["status", "processing_started_at"])

        payload = unwrap_payload(log.payload)
        batch   = payload.get("rows")
        rows    = [r for r in (batch if isinstance(batch, list) else [payload])
                   if isinstance(r, dict) and r]

        if not rows:
            return self._fail(
                'No paper review data in the payload. Send one JSON object of '
                'fields, or {"rows": [ ... ]}.', started)
        if len(rows) > MAX_ROWS:
            return self._fail(
                f"{len(rows)} rows in one delivery; the limit is {MAX_ROWS}. "
                f"Split the batch.", started)

        # Sorted so the same delivery reports its unrecognised columns in the
        # same order twice — these keys come from a set union across rows.
        mapping, unrecognised = map_headers(
            sorted({k for row in rows for k in row}))
        unrecognised = sorted(unrecognised)

        # Duplicate warnings, scoped by the emails actually in this delivery
        # rather than by a walk of the whole table. Lower() on both sides because
        # the stored spelling of an address is not the sender's.
        emails = {as_text(normalise_row(r, mapping).get("email")).lower()
                  for r in rows}
        emails.discard("")
        existing_pairs = set(
            PaperReview.objects.annotate(email_lower=Lower("email"))
            .filter(email_lower__in=emails)
            .values_list("email_lower", "event_code")
        ) if emails else set()

        plan       = classify_rows(rows, mapping, WEBHOOK_USER, existing_pairs)
        counts     = summarise(plan)
        importable = [e for e in plan if e["classification"] != ERROR]

        # Per-object save() inside one atomic block, exactly as import/commit/
        # does: PaperReview.save() recomputes proposal_score and grade from the
        # criteria, which bulk_create would skip, and a failure anywhere rolls the
        # whole delivery back rather than leaving half a batch behind.
        created_ids = []
        with transaction.atomic():
            for entry in importable:
                obj = PaperReview(**entry["_payload"])
                obj.save()
                created_ids.append(obj.id)

        created = len(created_ids)
        skipped = counts[ERROR]
        self._note(f"{len(rows)} row(s) in; {created} created, {skipped} rejected.")
        if unrecognised:
            self._note(f"Unrecognised columns ignored: {unrecognised}")

        log.records_inserted = created
        log.records_failed   = skipped
        if created and skipped:
            log.db_insert_status = WebhookLog.DbInsertStatus.PARTIAL
        elif created:
            log.db_insert_status = WebhookLog.DbInsertStatus.INSERTED
        else:
            log.db_insert_status = WebhookLog.DbInsertStatus.FAILED

        # The first accepted row's event, so the delivery is identifiable in the
        # logs table without opening the payload — the column the booking webhook
        # fills from the invoice.
        if importable:
            log.event_code = importable[0]["event_code"]

        success = created > 0
        log.status = (WebhookLog.Status.SUCCESS if success
                      else WebhookLog.Status.FAILED)
        log.processing_status = (WebhookLog.ProcessingStatus.PROCESSED if success
                                 else WebhookLog.ProcessingStatus.ERROR)
        self._finish(
            started=started,
            http_status=201 if success else 400,
            error="" if success else "No row could be imported.",
        )

        return success, {
            # Read by the view: "inserted" is what turns the response into a 201.
            "db_action":   "inserted" if success else "",
            "created":     created,
            "created_ids": created_ids,
            "skipped":     skipped,
            "counts":      counts,
            "unrecognised_columns": unrecognised,
            "workflows_suppressed": {
                "proposal_submission": True,
                "production_team_email": True,
            },
            "rows": public_plan(plan),
        }
