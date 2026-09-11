"""
Freeze a stamped badge list, for any number of events, as one dated run each.

WHAT THIS IS
Four columns, Event, Name, Company, Stamp. Every row sharing an event and a
stamp is one badge run, written into pre_event_docs_badge_issues with issued_at
set to the STAMP rather than to the time this command runs. That is the whole
job; the page reads those runs as the frozen badge table and diffs the current
bookings against them.

HOW THIS DIFFERS FROM import_badge_log, and why both exist
import_badge_log imports the workbook's Database_Sent tab, whose Stamp column is
empty in every row. With no date it can only group one synthetic run per event
and date it at the import. This one is for a list that CARRIES its stamps, so
the run boundary is a real event and a real moment, and the history reads as the
dates the badges actually went out. Neither command is a replacement for the
other and this one does not touch the other's behaviour.

IDEMPOTENT ON THE RUN, not on the row. A group whose event already holds badges
at that exact stamp is skipped whole, so re-running an extended file imports only
what is new. That is also why the same person can appear in two runs, which is
the normal case, a reprint; row level skipping would silently drop the second.

WHAT HAPPENS TO A ROW WITH NO BOOKING, and why it still goes in
It is imported with a null delegate, because the point of a run is to record the
list as sent. Dropping it would make that person read as a New Badge on the next
run and reprint them.

THE COST OF THAT, stated plainly: a badge with no delegate lands on the
Cancellations list captioned "Booking removed", see services.badge_changes. That
caption is a CRM data gap and not a real cancellation. It clears for a row the
moment the matching booking exists in Bookings, so the cure is delegate rows,
not a different import. --matched-only leaves them out for whoever would rather
have a short run than a false cancellation.

    python manage.py import_badge_runs --file badges.tsv --dry-run
    python manage.py import_badge_runs --file badges.tsv
    python manage.py import_badge_runs --sheet-id <id> --tab Database_Sent
"""
import csv
import operator
import re
import uuid
from datetime import datetime
from functools import reduce

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from book_delegate.models import BookDelegate
from pre_event_docs.models import BadgeIssue

COL_EVENT, COL_NAME, COL_COMPANY, COL_STAMP = 0, 1, 2, 3

# Both spellings the supplied lists use, with and without seconds. The month and
# the day arrive separated by a stray space in some rows, "May -13-2026", which
# is squeezed out before either format is tried.
STAMP_FORMATS = ("%b-%d-%Y at %I:%M:%S %p", "%b-%d-%Y at %I:%M %p",
                 # A workbook cell holding a real date rather than text.
                 "%Y-%m-%d %H:%M:%S")

# Titles dropped for the SECOND matching attempt only. The badge still stores the
# name as printed; this only widens the lookup, and only when the first attempt
# found nothing and the company still matches exactly, so a title cannot bind a
# badge to somebody else's booking.
TITLES = {"dr", "mr", "mrs", "ms", "miss", "prof", "professor"}


def key(value):
    """Collapsed whitespace, folded case. The only comparison used here."""
    return " ".join((value or "").split()).casefold()


def clean(value):
    """
    Collapsed whitespace.

    This is what repairs the rows that arrive quoted and split across a tab,
    "Paul\t Solano", which the csv reader hands back as one field with the tab
    still in it.
    """
    return " ".join((value or "").split())


def without_title(name):
    """The name with a leading Dr, Mr, Prof and so on removed, or unchanged."""
    parts = name.split()
    if len(parts) > 2 and parts[0].rstrip(".").casefold() in TITLES:
        return " ".join(parts[1:])
    return name


def parse_stamp(raw):
    """The Stamp column as an aware datetime, or None when it is not a stamp."""
    text = re.sub(r"\s*-\s*", "-", clean(raw))
    for fmt in STAMP_FORMATS:
        try:
            return timezone.make_aware(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


class Command(BaseCommand):
    help = "Freeze a stamped Event/Name/Company/Stamp badge list as one dated run per event."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="TSV or CSV, columns Event, Name, Company, Stamp.")
        parser.add_argument("--sheet-id", help="Spreadsheet id to read instead of a file.")
        parser.add_argument("--tab", default="Database_Sent", help="Worksheet name, with --sheet-id.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would be written and write nothing.")
        parser.add_argument("--matched-only", action="store_true",
                            help="Leave out rows with no booking; see the module docstring.")
        parser.add_argument("--replace", action="store_true",
                            help=("Delete every existing badge row on the events in this "
                                  "file first. Needed only to redo an import."))

    def handle(self, *args, **opts):
        if bool(opts["file"]) == bool(opts["sheet_id"]):
            raise CommandError("Give exactly one of --file or --sheet-id.")
        rows = (self._read_file(opts["file"]) if opts["file"]
                else self._read_sheet(opts["sheet_id"], opts["tab"]))
        if not rows:
            raise CommandError("No rows found.")

        # The header is dropped only when it IS a header. A file pasted without
        # one would otherwise lose its first badge, silently.
        if rows and key(rows[0][COL_EVENT] if rows[0] else "") == "event":
            rows = rows[1:]

        groups, blank, bad_stamps, duplicates = {}, 0, {}, 0
        for row in rows:
            def cell(i):
                return clean(row[i]) if len(row) > i else ""
            event, name, company, stamp = (cell(COL_EVENT), cell(COL_NAME),
                                           cell(COL_COMPANY), cell(COL_STAMP))
            if not (event and name):
                blank += 1
                continue
            issued_at = parse_stamp(stamp)
            if issued_at is None:
                # Reported and skipped rather than dated today. A run whose date
                # is wrong is worse than a run that was never written, because
                # nothing downstream can tell that it is wrong.
                bad_stamps.setdefault(stamp or "(empty)", 0)
                bad_stamps[stamp or "(empty)"] += 1
                continue
            members = groups.setdefault((event, issued_at), {})
            if (key(name), key(company)) in members:
                # The same badge twice in one run is a spreadsheet artefact, not
                # two badges. Across runs it is a reprint and is kept; see the
                # module docstring.
                duplicates += 1
                continue
            members[(key(name), key(company))] = (name, company)

        self.stdout.write(f"{len(rows)} rows, {len(groups)} runs, "
                          f"{blank} blank, {duplicates} repeated within a run")
        if bad_stamps:
            for stamp, count in sorted(bad_stamps.items(), key=lambda kv: -kv[1])[:10]:
                self.stdout.write(self.style.ERROR(f"  unreadable stamp: {stamp!r} x{count}"))
            raise CommandError(
                f"{sum(bad_stamps.values())} rows carry a stamp this cannot read. "
                f"Expected {STAMP_FORMATS[0]!r}. Nothing was written.")

        events = {event for event, _ in groups}
        # iexact per code rather than __in, which is case sensitive. A file whose
        # codes differ only in case would otherwise match no booking at all and
        # report every row as a data gap; the rest of the module compares event
        # codes case insensitively for the same reason.
        delegates = BookDelegate.objects.filter(
            reduce(operator.or_, (Q(event_code__iexact=e) for e in events)))
        if not delegates.exists():
            self.stdout.write(self.style.WARNING(
                "no bookings found for any event code in this file; check the spelling"))

        # One index for the whole file. Both spellings of the company are keyed,
        # the linked record and the raw text, because a list is typed against
        # whichever the desk saw.
        index, by_name, editions = {}, {}, {}
        for code, pk, first, last, raw, company in delegates.values_list(
            "event_code", "id", "first_name", "last_name",
            "company_name_raw", "company__name",
        ):
            for spelling in {key(company), key(raw)} - {""}:
                index.setdefault((key(code), key(f"{first} {last}"), spelling), []).append(pk)
            by_name.setdefault((key(code), key(f"{first} {last}")), []).append(pk)
        for code, edition in delegates.values_list("event_code", "edition"):
            editions.setdefault(key(code), set()).add(edition)

        existing_runs = {
            (key(code), stamp)
            for code, stamp in BadgeIssue.objects.values_list("event_code", "issued_at")
        }

        planned, skipped_runs = [], 0
        for (event, issued_at), members in sorted(groups.items(), key=lambda kv: kv[0]):
            if (key(event), issued_at) in existing_runs and not opts["replace"]:
                skipped_runs += 1
                self.stdout.write(f"  {event} {issued_at:%Y-%m-%d %H:%M}  already imported, skipped")
                continue

            found = editions.get(key(event), set())
            edition = found.pop() if len(found) == 1 else None

            matched, unmatched = [], []
            for name, company in members.values():
                hits = (index.get((key(event), key(name), key(company)))
                        # Last, the name alone. The company is what the BADGE
                        # printed and the booking spells it differently often
                        # enough, "Wiggl" against "Wiggl Health", "SAE-ITC"
                        # against the name spelled out. Accepted only when
                        # exactly one booking at this event carries the name, so
                        # a spelling can never bind somebody else's badge; two
                        # people of one name stay null, as they do above.
                        or index.get((key(event), key(without_title(name)), key(company)))
                        or by_name.get((key(event), key(name)))
                        or by_name.get((key(event), key(without_title(name))))
                        or [])
                # Two bookings under one name and company is not a choice this
                # makes for somebody; that badge goes in with no delegate.
                (matched if len(hits) == 1 else unmatched).append(
                    (name, company, hits[0] if len(hits) == 1 else None))

            planned.append((event, issued_at, edition, matched, unmatched))
            self.stdout.write(
                f"  {event} {issued_at:%Y-%m-%d %H:%M}  edition {edition}, "
                f"{len(members)} badges, {len(matched)} matched, {len(unmatched)} with no booking")

        total = sum(len(m) + (0 if opts["matched_only"] else len(u))
                    for _, _, _, m, u in planned)
        orphans = sum(len(u) for _, _, _, _, u in planned)
        if orphans and not opts["matched_only"]:
            self.stdout.write(self.style.WARNING(
                f'{orphans} badges will carry no booking and will read "Booking removed" '
                "on Cancellations until those bookings exist"))

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING(
                f"dry run, nothing written; {total} badges in {len(planned)} runs, "
                f"{skipped_runs} runs already imported"))
            return
        if not planned:
            self.stdout.write("nothing to write")
            return

        with transaction.atomic():
            if opts["replace"]:
                deleted, _ = BadgeIssue.objects.filter(
                    event_code__in={event for event, _, _, _, _ in planned}).delete()
                self.stdout.write(f"cleared {deleted} existing badge rows")
            for event, issued_at, edition, matched, unmatched in planned:
                run_id = uuid.uuid4()
                BadgeIssue.objects.bulk_create([
                    BadgeIssue(
                        delegate_id=pk, event_code=event, edition=edition,
                        name=name[:255], company=company[:255],
                        # issued_by stays null, which is what marks a run as
                        # imported history rather than something printed here.
                        issued_at=issued_at, run_id=run_id,
                    )
                    for name, company, pk in (matched if opts["matched_only"]
                                              else matched + unmatched)
                ], batch_size=1000)

        self.stdout.write(self.style.SUCCESS(
            f"froze {total} badges as {len(planned)} runs, "
            f"{skipped_runs} already imported and left alone"))

    # ── sources ──────────────────────────────────────────────────────────────

    def _read_file(self, path):
        """
        A workbook, or tab separated by default and comma when the file says so.

        Sniffed on the first line rather than asked for, because the lists are
        pasted out of a sheet and arrive as either. quotechar is the reader's
        default, which is what keeps a name that was quoted around a tab whole.
        """
        if path.lower().endswith((".xlsx", ".xlsm")):
            return self._read_workbook(path)
        with open(path, newline="", encoding="utf-8-sig") as fh:
            first = fh.readline()
            fh.seek(0)
            delimiter = "," if first.count(",") > first.count("\t") else "\t"
            return [row for row in csv.reader(fh, delimiter=delimiter)]

    def _read_workbook(self, path):
        """
        The first sheet of an xlsx, every cell as text.

        openpyxl is already a dependency, so a workbook costs no export step and
        the lists arrive as one either way. Stringified rather than typed,
        because the only column that is not text is the stamp and parse_stamp
        reads a date written both ways.
        """
        import openpyxl
        sheet = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
        return [["" if cell is None else str(cell) for cell in row]
                for row in sheet.iter_rows(values_only=True)]

    def _read_sheet(self, sheet_id, tab):
        from reports.services.connector import GoogleSheetsConnector
        self.stdout.write(f"reading {tab} from {sheet_id} (read-only scope)")
        return GoogleSheetsConnector().read_worksheet(sheet_id, tab)
