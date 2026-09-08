"""
Import the workbook's Database_Sent tab into pre_event_docs_badge_issues.

WHY THIS HAS TO RUN BEFORE THE PAGE IS USED FOR REAL
With an empty badge log every current registrant reads as a new badge, so the
first badge run would reprint the entire event. The workbook has 5,318 rows of
history and it is the only record that those badges were ever issued.

WHY IT READS THE SHEET RATHER THAN A FILE
Because it can, and an export step is a step somebody has to remember. The
service account behind reports/services/connector.py is scoped
spreadsheets.readonly, so this command CANNOT write to the workbook even by
mistake. A --file is accepted as well, for the day the sheet is gone.

WHAT IT DOES WITH A ROW IT CANNOT MATCH, and why that changed
It COUNTS it and does not import it, unless --include-unmatched is passed.

That is not the obvious choice, so here is the measurement behind it. Of 5,257
distinct log rows, 3,423 match a delegate and 1,834 do not, and the unmatched
ones are not spelling drift. Every event in the log has delegates in the CRM,
but the log holds far more badges than the CRM holds delegates: PSE - MP has 121
logged badges against 37 delegate rows, DLE - VV has 79 against 12. So those
badges belong to bookings the CRM does not have at all.

Imported with a null delegate, each one would sit on its event take out list for
ever, captioned Booking removed. That caption would be wrong. The person is not
absent from the event, they are absent from the CRM, which is a data gap and not
a cancellation, and a desk cannot act on it. So they are reported and left out,
and --include-unmatched is there for whoever wants the full historical record.

Nothing here guesses. Matching is on event plus name plus company, compared on
collapsed whitespace and folded case, and a row matching two delegates is
reported rather than assigned to whichever came first.

IDEMPOTENT. Re-running imports nothing new, because a row is skipped when the
same event, name and company already carry a badge. That matters, because the
natural response to a confusing count is to run it again.
"""
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from book_delegate.models import BookDelegate
from pre_event_docs.models import BadgeIssue

TAB = "Database_Sent"
# The workbook's own column order: Event, Name, Company, Stamp.
COL_EVENT, COL_NAME, COL_COMPANY = 0, 1, 2


def key(value):
    return " ".join((value or "").split()).casefold()


class Command(BaseCommand):
    help = "Import the workbook Database_Sent badge history."

    def add_arguments(self, parser):
        parser.add_argument("--sheet-id", default="14eMXunPQGmOWuLPpfd4Lzrqny4ih28Brz0_fesqZQIE",
                            help="Spreadsheet id holding the Database_Sent tab.")
        parser.add_argument("--file", help="CSV fallback, same four columns.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would happen and write nothing.")
        parser.add_argument("--include-unmatched", action="store_true",
                            help=("Also import log rows with no delegate in the CRM. "
                                  "Off by default; see the module docstring."))

    def handle(self, *args, **opts):
        rows = self._read_file(opts["file"]) if opts["file"] else self._read_sheet(opts["sheet_id"])
        if not rows:
            raise CommandError("No rows found.")

        header, body = rows[0], rows[1:]
        self.stdout.write(f"{TAB}: {len(body)} rows, columns {header}")

        # One pass over the delegates, indexed the way the log is keyed. The log
        # carries no delegate id, so text is all there is to match on.
        index = {}
        for pk, code, first, last, raw, company in BookDelegate.objects.values_list(
            "id", "event_code", "first_name", "last_name",
            "company_name_raw", "company__name",
        ):
            k = (key(code), key(f"{first} {last}"), key(company or raw))
            index.setdefault(k, []).append(pk)

        existing = {
            (key(e), key(n), key(c))
            for e, n, c in BadgeIssue.objects.values_list("event_code", "name", "company")
        }

        matched, ambiguous, orphaned, skipped, blank = [], [], [], 0, 0
        for row in body:
            event = (row[COL_EVENT] if len(row) > COL_EVENT else "").strip()
            name = (row[COL_NAME] if len(row) > COL_NAME else "").strip()
            company = (row[COL_COMPANY] if len(row) > COL_COMPANY else "").strip()
            if not (event and name):
                blank += 1
                continue
            k = (key(event), key(name), key(company))
            if k in existing:
                skipped += 1
                continue
            existing.add(k)

            hits = index.get(k, [])
            if len(hits) == 1:
                matched.append((event, name, company, hits[0]))
            elif len(hits) > 1:
                # Reported, and imported with no delegate rather than guessed at.
                ambiguous.append((event, name, company, hits))
                orphaned.append((event, name, company))
            else:
                orphaned.append((event, name, company))

        keeping_orphans = opts["include_unmatched"]
        fate = ("importing" if keeping_orphans
                else "NOT imported, see --include-unmatched")
        self.stdout.write(
            f"  matched to a delegate : {len(matched)}\n"
            f"  no delegate found     : {len(orphaned)}  ({fate})\n"
            f"  ambiguous, left null  : {len(ambiguous)}\n"
            f"  duplicate in the log  : {skipped}\n"
            f"  blank rows ignored    : {blank}"
        )
        for event, name, company, hits in ambiguous[:10]:
            self.stdout.write(f"    ambiguous: {event} / {name} / {company} -> {hits}")

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("dry run, nothing written"))
            return

        # issued_at is NOT NULL and the workbook Stamp column is empty in every
        # row, so the import time is what goes in. No deadline is derived from
        # it, because the change window is measured from the EVENT date; see
        # services.change_deadline. This is why the empty Stamp column costs
        # nothing here.
        now = timezone.now()

        # ONE run_id PER EVENT, not per row.
        #
        # THE BUG THIS FIXES. run_id has `default=uuid.uuid4`, so constructing a
        # BadgeIssue without passing one gives every row its own. The first
        # import produced 3,423 rows with 3,423 distinct run_ids, which made the
        # Run History 177 separate one-badge runs for a single event and left
        # undo useless, since undoing a "run" removed one person.
        #
        # Per EVENT rather than one for the whole import, because a badge run is
        # an event-level act and the history is read per event. The workbook's
        # Database_Sent has no run concept at all, so this is the closest honest
        # grouping: one synthetic run per event, carrying no issued_by, which is
        # what marks it as imported history rather than something somebody here
        # printed.
        run_ids = {}

        def run_for(event):
            if event not in run_ids:
                run_ids[event] = uuid.uuid4()
            return run_ids[event]
        objects = [
            BadgeIssue(delegate_id=pk, event_code=event, name=name[:255],
                       company=company[:255], issued_at=now,
                       run_id=run_for(event))
            for event, name, company, pk in matched
        ] + ([
            BadgeIssue(delegate_id=None, event_code=event, name=name[:255],
                       company=company[:255], issued_at=now,
                       run_id=run_for(event))
            for event, name, company in orphaned
        ] if keeping_orphans else [])
        with transaction.atomic():
            BadgeIssue.objects.bulk_create(objects, batch_size=1000)
        self.stdout.write(self.style.SUCCESS(
            f"imported {len(objects)} badge records "
            f"as {len(run_ids)} runs, one per event"))

    # ── sources ──────────────────────────────────────────────────────────────

    def _read_sheet(self, sheet_id):
        from reports.services.connector import GoogleSheetsConnector
        connector = GoogleSheetsConnector()
        self.stdout.write(f"reading {TAB} from {sheet_id} (read-only scope)")
        return connector.read_worksheet(sheet_id, TAB)

    def _read_file(self, path):
        import csv
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return [row for row in csv.reader(fh)]
