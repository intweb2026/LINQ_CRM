"""
sync_verdicts_from_sheet
────────────────────────
Copies the Event Status column of the Weekly Event Data sheet into the
Performance Matrix verdict of the matching event.

    python manage.py sync_verdicts_from_sheet             dry run, prints the plan
    python manage.py sync_verdicts_from_sheet --apply     writes the verdicts

Column B of the tab carries the INTERNAL event code, which is matched against
Event.event_code case insensitively. Column BJ carries the status, which is
mapped onto Event.Verdict through STATUS_ALIASES below; anything the map does
not know is reported and left alone, never guessed.

The sheet is read through the same service account the Google Sync module
uses, so the spreadsheet has to be shared with that account, viewer is enough.
Writes go through queryset.update, exactly as the matrix verdict endpoint does,
because Event.save() re derives nine columns and re resolves the owner and a
verdict must touch none of that.
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from events.models import Event

DEFAULT_SHEET_ID = "1Zot_42szEFKKEo3EPRxqJk7R8rqoUkyVt47SfXVbbuc"
DEFAULT_TAB = "Weekly Event Data"
DEFAULT_CODE_COL = "B"
DEFAULT_STATUS_COL = "BJ"

# Lower cased sheet spelling to the stored verdict. The stored spellings are
# listed too, so a sheet that already uses them passes straight through.
STATUS_ALIASES = {
    "going ahead": Event.Verdict.GOING_AHEAD,
    "go ahead": Event.Verdict.GOING_AHEAD,
    "needs a push": Event.Verdict.NEEDS_PUSH,
    "needs push": Event.Verdict.NEEDS_PUSH,
    "push": Event.Verdict.NEEDS_PUSH,
    "full efforts req.": Event.Verdict.FULL_EFFORTS,
    "full efforts req": Event.Verdict.FULL_EFFORTS,
    "full efforts required": Event.Verdict.FULL_EFFORTS,
    "full efforts": Event.Verdict.FULL_EFFORTS,
    "postponed": Event.Verdict.POSTPONED,
    "tbp": Event.Verdict.TBP,
    "to be planned": Event.Verdict.TBP,
    "cancelled": Event.Verdict.CANCELLED,
    "canceled": Event.Verdict.CANCELLED,
    "standby": Event.Verdict.STANDBY,
    "stand by": Event.Verdict.STANDBY,
}


def normalise_status(raw):
    """The stored verdict a sheet cell means, or None when it is blank or unknown."""
    key = " ".join(str(raw or "").split()).strip().lower()
    if not key:
        return None
    return STATUS_ALIASES.get(key)


def column_index(letters):
    """Spreadsheet column letters to a zero based index, B is 1 and BJ is 61."""
    n = 0
    for ch in letters.strip().upper():
        if not ("A" <= ch <= "Z"):
            raise CommandError(f"{letters!r} is not a column reference.")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def plan_changes(rows, code_idx, status_idx):
    """
    What the sheet asks for, checked against the catalogue.

    Returns a dict with `changes` as (event, new verdict) pairs, `unchanged`
    as events already holding the value, `unmatched` as codes with no event,
    `unknown` as (code, raw status) pairs the alias map cannot place, and
    `blank` as the count of rows whose status cell is empty. Nothing is written.
    """
    by_code = {e.event_code.strip().upper(): e for e in Event.objects.all()}
    out = {"changes": [], "unchanged": [], "unmatched": [], "unknown": [], "blank": 0}
    seen = set()
    for row in rows:
        code = str(row[code_idx]).strip() if len(row) > code_idx else ""
        raw = row[status_idx] if len(row) > status_idx else ""
        if not code or code.upper() in seen:
            continue
        event = by_code.get(code.upper())
        if event is None:
            # Header rows and totals land here too, alongside real misses; the
            # caller prints them all so a typo in the sheet is visible.
            out["unmatched"].append(code)
            continue
        seen.add(code.upper())
        if not str(raw or "").strip():
            out["blank"] += 1
            continue
        verdict = normalise_status(raw)
        if verdict is None:
            out["unknown"].append((code, str(raw).strip()))
            continue
        if (event.verdict or "") == verdict:
            out["unchanged"].append(event)
        else:
            out["changes"].append((event, verdict))
    return out


def apply_changes(changes):
    """Writes the planned verdicts, one update per event, and returns the count."""
    now = timezone.now()
    for event, verdict in changes:
        Event.objects.filter(pk=event.pk).update(verdict=verdict, updated_at=now)
    return len(changes)


class Command(BaseCommand):
    help = "Copy the Weekly Event Data status column into the Performance Matrix verdicts."

    def add_arguments(self, parser):
        parser.add_argument("--sheet-id", default=DEFAULT_SHEET_ID, help="Spreadsheet id or URL.")
        parser.add_argument("--tab", default=DEFAULT_TAB, help="Worksheet name.")
        parser.add_argument("--code-col", default=DEFAULT_CODE_COL, help="Column holding the internal event code.")
        parser.add_argument("--status-col", default=DEFAULT_STATUS_COL, help="Column holding the status.")
        parser.add_argument("--apply", action="store_true", help="Write the verdicts. Without it nothing changes.")

    def handle(self, *args, **opts):
        from services.google_sheets import GoogleSheetsService

        code_idx = column_index(opts["code_col"])
        status_idx = column_index(opts["status_col"])
        last_col = opts["status_col"] if status_idx >= code_idx else opts["code_col"]

        try:
            sheets = GoogleSheetsService(opts["sheet_id"])
            result = sheets.service.spreadsheets().values().get(
                spreadsheetId=sheets.spreadsheet_id,
                range=f"'{opts['tab']}'!A:{last_col.upper()}",
            ).execute()
        except FileNotFoundError as exc:
            raise CommandError(str(exc))
        except Exception as exc:
            raise CommandError(
                f"Could not read the sheet: {exc}. Share it with the service account "
                f"named in GOOGLE_SHEETS_CREDENTIALS, viewer access is enough."
            )
        rows = result.get("values", [])
        if not rows:
            raise CommandError("The tab returned no rows.")

        plan = plan_changes(rows, code_idx, status_idx)
        w = self.stdout.write

        w(f"Read {len(rows)} rows from '{opts['tab']}', codes in {opts['code_col']}, status in {opts['status_col']}.")
        w(f"Matched events           : {len(plan['changes']) + len(plan['unchanged']) + plan['blank'] + len(plan['unknown'])}")
        w(f"Verdicts to change       : {len(plan['changes'])}")
        w(f"Already correct          : {len(plan['unchanged'])}")
        w(f"Blank status, skipped    : {plan['blank']}")
        w(f"Unknown status, skipped  : {len(plan['unknown'])}")
        w(f"Codes with no event      : {len(plan['unmatched'])}")
        for event, verdict in plan["changes"]:
            w(f"  {event.event_code:<24} {event.verdict or 'Standby':<20} -> {verdict}")
        for code, raw in plan["unknown"]:
            w(f"  UNKNOWN  {code:<24} {raw!r}")
        for code in plan["unmatched"]:
            w(f"  NO EVENT {code}")

        if not opts["apply"]:
            w(self.style.WARNING("Dry run. Re run with --apply to write these verdicts."))
            return
        n = apply_changes(plan["changes"])
        w(self.style.SUCCESS(f"Updated {n} verdicts."))
