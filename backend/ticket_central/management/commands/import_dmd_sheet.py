"""
import_dmd_sheet
─────────────────
Load a LinkedIn ticket workbook into Ticket Central, updating the rows already
in the table and creating the ones that are not.

    python manage.py import_dmd_sheet data_imports/ticket_central_LX.xlsx
    python manage.py import_dmd_sheet data_imports/ticket_central_LX.xlsx --out report.csv
    python manage.py import_dmd_sheet data_imports/ticket_central_LX.xlsx --commit

THE WORKBOOK IS IN THE REPO, at backend/data_imports/ticket_central_LX.xlsx, so
a deploy carries it to production and the path above works unchanged there. It
is force-added past the repo-wide *.xlsx ignore rule, which is how the three
workbooks already in that directory got there.

.xlsx, .xlsm, .csv and .json are all accepted, through the shared reader in
accounts/import_common.py. Prefer the workbook over a CSV export of it. openpyxl
hands over typed dates, while a CSV carries text, and utils._parse_date has no
format with a space before the time, so an Assign Date exported as
"2026-08-18 00:00:00" silently lands empty; a CSV also makes "08/09/2026"
ambiguous, and it is read day-first. ISO yyyy-mm-dd date columns are safe.

WRITES NOTHING WITHOUT --commit, which inverts the repo's usual --dry-run flag
on purpose; this loads thousands of rows in one pass, so the default has to be
the harmless one. `fix_ticket_number_types` already uses --apply the same way.

HOW A SHEET ROW FINDS ITS TICKET
On the link, normalised, which is `Ticket.link_key`; the same digest the
repeated-link check reads, so a scheme difference, a "www." or a trailing slash
cannot hide a match. `external_id` is NULL on all 47,172 live rows, so the
Smart Import upsert path has nothing to match on and this command exists
instead.

One link can legitimately sit on several tickets, 480 links do in the live
table, so Contact Purpose breaks the tie; a link that still resolves to more
than one ticket is REPORTED AND SKIPPED rather than guessed at. Picking one at
random would write a person's mining result onto the wrong ticket, and nothing
downstream would ever say so.

WHAT AN UPDATE TOUCHES
Only the columns whose value actually differs from what is stored, so a row
already correct is left alone and its Modified Time is not disturbed. A blank
cell never clears a stored value; `_coerce_row` drops empty cells, which is what
makes a partly filled sheet safe to re-run.

Deliberately NOT written on an update.

  created_at          the stored Added Time is that row's real history; Entry
                      Date is honoured on a new ticket, never over an old one.
  mr_submitted_at     follows a status this command did not set.

STATUS, AND WHY NOT THE IMPORTER'S RULE
`utils.infer_status_from_row` calls any Data Mining field completed, assignment
included. This sheet assigns long before it reports, 697 of its new rows name an
assignee with no result yet, so that rule would mark unfinished work complete.
The rule here is the one `mining_matrix` already applies, a ticket is worked when
`actual_number` is present, and nothing else moves a status. An update can
therefore promote mr_submitted to completed; it can never move a status
backwards, and a returned ticket is left where the person who returned it put
it.

REPEATED LINKS IN THE FILE
The 2026-09 LX workbook holds 498 links twice, always as a pair sharing one
purpose. 310 of those pairs resolve to a single existing ticket, so the pair is
merged, later non-blank value winning, and every one of them is listed in the
CSV under `repeat_of` with the fields that disagreed. The other 188 pairs are
two new tickets, which is what the sheet says they are.

AFTER A --commit RUN
`created_by` is NULL and `added_user_text` is blank on everything created here,
because a management command has no request user, and Ticket Central scopes its
list by those two columns. Run `map_added_users` afterwards to stamp the owner
from the purpose; it already owns all 84 purposes this workbook uses.
"""
import csv
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ticket_central.models import Ticket
from ticket_central.utils import (
    _coerce_row, assign_next_ticket_number, derive_audit_timestamps,
    extract_purpose_code, link_digest, normalize_purpose,
)

logger = logging.getLogger(__name__)

# Sheet header, then the Ticket field it lands on. Matched folded, so case,
# spaces and punctuation in the header do not have to be exact.
#
# The three that are not a literal reading of the header, and the evidence.
#   Entry Date   created_at, the row's Added Time, not hubspot_entry_date. It
#                leads the Market Research block in the sheet, and the grid's
#                own first column is created_at labelled Added Time.
#   Total        actual_number, the Data Mining result. Live counts settle it,
#                actual_number is filled on 31,028 rows and mined_count on 53,
#                and mining_matrix defines unmined as actual_number IS NULL, so
#                Total anywhere else leaves finished work in the mining queue.
#   New Entry    new_contacts_created, filled on 30,895 rows, the column that
#                sits beside actual_number in the same block.
HEADER_MAP = {
    "Entry Date":             "created_at",
    "Contact Purpose":        "purpose",
    "LinkedIn Search Link":   "link_url",
    "LinkedIn Keywords Used": "linkedin_keywords",
    "Estimate No.":           "estimate",
    "Type of Ticket":         "type_of_ticket",
    "DM Comments":            "dm_comments",
    "Assign name":            "assign_name",
    "Assign Date":            "assign_date",
    "New Entry":              "new_contacts_created",
    "Total":                  "actual_number",
    "Complete Date":          "complete_date",
}

# The columns an update may write, which is every mapped field except the one
# that carries history. Order fixed so the CSV reads the same way every run.
UPDATABLE = [f for f in HEADER_MAP.values() if f != "created_at"]

# Set by _coerce_row from the row's own values; this command decides all four
# for itself, see the docstring.
_COERCED_EXTRAS = (
    "status", "mr_submitted_at", "dmd_submitted_at", "_modified_time",
)

# The Data Mining result. Present means the link was worked, which is the
# definition mining_matrix already uses for the unmined queue.
RESULT_FIELD = "actual_number"

# Assignee columns are run through normalize_dmd_assignees' own rule before
# being written. NOT cosmetic, and not optional.
#
# That command has already been run against the live table, so 0 of 47,172 rows
# carry a code prefix and every assignee sits under one canonical spelling. This
# workbook still carries the raw source spellings, "SC - Aastha Panchal" on every
# row. Writing them as they stand would put the prefix back on 2,180 rows, and
# since the only link between a person and their tickets is the name STRING,
# those rows would drop out of that person's dropdown, filters and reports on the
# way in. Reusing `rewrite` rather than copying the regex is what keeps the two
# from drifting; it returns None when a value is already canonical, hence the
# `or value`.
ASSIGNEE_FIELDS = ("assign_name",)

VERDICT_UPDATE = "update"
VERDICT_UNCHANGED = "unchanged"
VERDICT_NEW = "new"
VERDICT_AMBIGUOUS = "ambiguous"
VERDICT_NO_LINK = "no_link"
VERDICTS = (VERDICT_UPDATE, VERDICT_UNCHANGED, VERDICT_NEW,
            VERDICT_AMBIGUOUS, VERDICT_NO_LINK)


def _fold(value):
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


_FOLDED_HEADERS = {_fold(header): field for header, field in HEADER_MAP.items()}


def read_sheet(path):
    """
    [(file_row_number, {field: value})], plus the headers that mapped to
    nothing.

    The file is read by accounts.import_common.read_import_rows, which is the
    shared server-side reader the paper-review and delegate-number commands
    already use, so .xlsx, .xlsm, .csv and .json all work and an Excel serial
    date is seen as the date it is. A CSV export of the workbook imports
    identically, which is the answer when the workbook itself cannot be put on
    the machine running this.

    `file_row_number` counts the header as row 1, so it is the Excel row number
    for any file with no blank rows inside the data; the shared reader skips a
    wholly blank row, and one of those would shift every number after it.

    A blank or whitespace-only cell is dropped rather than passed on as an empty
    string. _coerce_row keeps a whitespace-only cell as "", and on an update that
    would read as a change and CLEAR the stored value.
    """
    from accounts.import_common import read_import_rows

    raw_rows = read_import_rows(path)
    if not raw_rows:
        raise CommandError(f"No data rows in {path}.")

    # Union across rows, not the first row's keys. The xlsx reader builds each
    # row dict from min(len(header), len(values)), so a first data row with an
    # empty trailing cell would otherwise hide that column's header entirely.
    labels = list(dict.fromkeys(label for raw in raw_rows for label in raw))
    fields = {label: _FOLDED_HEADERS.get(_fold(label)) for label in labels}
    unmapped = [label for label, field in fields.items() if field is None]
    if "link_url" not in fields.values():
        raise CommandError(
            "No LinkedIn Search Link column found; that column is how a row "
            f"finds its ticket. Headers read, {labels}"
        )

    out = []
    for offset, raw in enumerate(raw_rows, start=2):
        row = {}
        for label, value in raw.items():
            field = fields.get(label)
            if field is None or value is None:
                continue
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            row[field] = value
        if row:
            out.append((offset, row))
    return out, unmapped


def _status_for(actual_number):
    """The status a row's own data implies. See the docstring."""
    return "completed" if actual_number not in (None, "") else "mr_submitted"


def canonicalise_assignees(coerced):
    """Fold the sheet's assignee spellings onto the stored ones, in place."""
    from ticket_central.management.commands.normalize_dmd_assignees import rewrite

    for field in ASSIGNEE_FIELDS:
        value = coerced.get(field)
        if value:
            coerced[field] = rewrite(value) or value
    return coerced


def plan(rows):
    """
    [{...}] in sheet order, one entry per sheet row, each carrying its verdict
    and the exact write it would make.

    One query for the whole file, on the indexed link_key column. Rows are
    planned in sheet order and an update sees the values a previous row in the
    same run already applied, so a repeated link merges instead of the second
    row's blanks reading as "no change".
    """
    digests = {file_row: link_digest(row.get("link_url", ""))
               for file_row, row in rows}

    fields = ("id", "link_key", "purpose", "ticket_number", "status",
              *UPDATABLE)
    by_key = {}
    wanted = {d for d in digests.values() if d}
    if wanted:
        for ticket in Ticket.objects.filter(link_key__in=wanted).values(*fields):
            by_key.setdefault(ticket["link_key"], []).append(ticket)

    # Sheet row that last claimed a given ticket, so a repeated link is reported
    # as the pair it is.
    claimed = {}
    # Running view of each ticket, so the second row of a pair diffs against
    # what the first row set rather than against the stored value.
    working = {}

    out = []
    for file_row, row in rows:
        entry = {
            "file_row": file_row, "row": row, "verdict": None,
            "ticket": None, "candidates": [], "payload": {}, "changed": [],
            "repeat_of": None, "conflicts": [],
        }
        digest = digests[file_row]
        candidates = by_key.get(digest, []) if digest else []
        entry["candidates"] = candidates

        if not digest:
            entry["verdict"] = VERDICT_NO_LINK
            out.append(entry)
            continue

        ticket = None
        if len(candidates) == 1:
            ticket = candidates[0]
        elif len(candidates) > 1:
            # Several tickets on one link. Contact Purpose decides, and only if
            # it decides outright.
            purpose = normalize_purpose(row.get("purpose", ""))
            same = [c for c in candidates
                    if normalize_purpose(c["purpose"]) == purpose]
            if len(same) == 1:
                ticket = same[0]

        if candidates and ticket is None:
            entry["verdict"] = VERDICT_AMBIGUOUS
            out.append(entry)
            continue

        coerced = canonicalise_assignees(_coerce_row(dict(row)))
        for key in _COERCED_EXTRAS:
            coerced.pop(key, None)
        preserved_created_at = coerced.pop("_preserved_created_at", None)

        if ticket is None:
            entry["verdict"] = VERDICT_NEW
            coerced["status"] = _status_for(coerced.get(RESULT_FIELD))
            coerced["_preserved_created_at"] = preserved_created_at
            entry["payload"] = coerced
            out.append(entry)
            continue

        entry["ticket"] = ticket
        stored = working.get(ticket["id"], ticket)
        if ticket["id"] in claimed:
            entry["repeat_of"] = claimed[ticket["id"]]
        claimed[ticket["id"]] = file_row

        payload = {}
        for field in UPDATABLE:
            if field not in coerced:
                continue
            if coerced[field] == stored[field]:
                continue
            if stored[field] not in (None, ""):
                entry["conflicts"].append(field)
            payload[field] = coerced[field]

        if payload:
            # link_key and updated_at are set by hand because a queryset update
            # never calls Model.save(), where the digest is derived and auto_now
            # fires. Without the digest an update that changes the link leaves
            # the old one behind and the repeated-link check stops seeing that
            # ticket.
            if "link_url" in payload:
                payload["link_key"] = link_digest(payload["link_url"])
            merged_result = payload.get(RESULT_FIELD, stored[RESULT_FIELD])
            if (merged_result not in (None, "")
                    and stored["status"] in ("draft", "mr_submitted")):
                payload["status"] = "completed"
                payload["dmd_submitted_at"] = derive_audit_timestamps(
                    {"complete_date": payload.get("complete_date")
                     or stored["complete_date"]},
                    "completed",
                )["dmd_submitted_at"] or timezone.now()

        entry["payload"] = payload
        entry["changed"] = [f for f in payload if f in UPDATABLE]
        entry["verdict"] = VERDICT_UPDATE if payload else VERDICT_UNCHANGED
        working[ticket["id"]] = {**stored, **payload}
        out.append(entry)
    return out


def apply_update(entry):
    payload = dict(entry["payload"])
    payload["updated_at"] = timezone.now()
    Ticket.objects.filter(pk=entry["ticket"]["id"]).update(**payload)


def apply_insert(entry):
    coerced = dict(entry["payload"])
    preserved_created_at = coerced.pop("_preserved_created_at", None)
    coerced.update(derive_audit_timestamps(
        {"_preserved_created_at": preserved_created_at,
         "complete_date": coerced.get("complete_date")},
        coerced["status"],
    ))
    # ponytail: assign_next_ticket_number re-reads every ticket_number for the
    # purpose to find the high-water mark, so a 2,900 row load does 2,900 scans
    # of an indexed column. Cache the used set per purpose if it drags.
    if not coerced.get("ticket_number"):
        purpose_code = extract_purpose_code(coerced.get("purpose", ""))
        if purpose_code:
            coerced["ticket_number"] = assign_next_ticket_number(
                purpose_code, coerced.get("type_of_ticket", ""),
            )
    ticket = Ticket.objects.create(**coerced)
    # created_at is auto_now_add, so a queryset update is the only way to hold
    # the sheet's Entry Date; same story as the Smart Import create branch.
    if preserved_created_at:
        Ticket.objects.filter(pk=ticket.pk).update(
            created_at=preserved_created_at)
    return ticket


class Command(BaseCommand):
    help = ("Import a LinkedIn ticket workbook, updating tickets matched on "
            "their link and creating the rest. Reports only, unless --commit.")

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the workbook or CSV")
        parser.add_argument(
            "--commit", action="store_true",
            help="Actually write. Without this the command only reports.",
        )
        parser.add_argument(
            "--out", default=None,
            help="Write the per-row verdicts to this CSV path.",
        )

    def handle(self, *args, **opts):
        path = opts["path"]
        rows, unmapped = read_sheet(path)
        if not rows:
            raise CommandError(f"No data rows in {path}.")
        entries = plan(rows)

        counts = dict.fromkeys(VERDICTS, 0)
        for entry in entries:
            counts[entry["verdict"]] += 1
        matched = counts[VERDICT_UPDATE] + counts[VERDICT_UNCHANGED]
        repeats = [e for e in entries if e["repeat_of"]]
        conflicts = [e for e in entries if e["conflicts"]]
        promoted = [e for e in entries
                    if e["payload"].get("status") == "completed"
                    and e["verdict"] == VERDICT_UPDATE]
        new_completed = [e for e in entries if e["verdict"] == VERDICT_NEW
                         and e["payload"].get("status") == "completed"]

        w = self.stdout.write
        w("")
        w(f"file                     {path}")
        w(f"data rows                {len(rows):>6,}")
        if unmapped:
            w(f"unmapped headers         {unmapped}")
        w("")
        w(f"already in the DB        {matched:>6,}  matched on Link URL")
        w(f"  of those, will change  {counts[VERDICT_UPDATE]:>6,}")
        w(f"  of those, already same {counts[VERDICT_UNCHANGED]:>6,}  no write")
        w(f"not in the DB            {counts[VERDICT_NEW]:>6,}  will create")
        w(f"ambiguous                {counts[VERDICT_AMBIGUOUS]:>6,}  skipped, "
          "link on several tickets and the purpose does not decide")
        w(f"no link in the row       {counts[VERDICT_NO_LINK]:>6,}  skipped, "
          "nothing to match on")
        w("")
        w(f"second line of a repeated link  {len(repeats):>6,}  merged into the "
          "same ticket")
        w(f"cells overwriting a stored value {len(conflicts):>5,}")
        w(f"promoted to completed           {len(promoted):>6,}  update rows "
          "that now carry a Total")
        w(f"created as completed            {len(new_completed):>6,}  new rows "
          "that carry a Total")
        w("")

        per_field = dict.fromkeys(UPDATABLE, 0)
        for entry in entries:
            for field in entry["changed"]:
                per_field[field] += 1
        w("columns an update would write")
        for field in UPDATABLE:
            w(f"  {field:<22}{per_field[field]:>7,}")
        w("")

        by_purpose = {}
        for entry in entries:
            key = normalize_purpose(entry["row"].get("purpose", "")) or "(blank)"
            bucket = by_purpose.setdefault(key, dict.fromkeys(VERDICTS, 0))
            bucket[entry["verdict"]] += 1
        w(f"{'purpose':<10}{'update':>8}{'unchgd':>8}{'new':>8}{'ambig':>7}"
          f"{'nolink':>8}")
        for key in sorted(by_purpose):
            b = by_purpose[key]
            w(f"{key[:9]:<10}{b[VERDICT_UPDATE]:>8,}{b[VERDICT_UNCHANGED]:>8,}"
              f"{b[VERDICT_NEW]:>8,}{b[VERDICT_AMBIGUOUS]:>7,}"
              f"{b[VERDICT_NO_LINK]:>8,}")
        w("")

        if opts["out"]:
            self._write_csv(opts["out"], entries)
            w(f"per-row verdicts written to {opts['out']}")
            w("")

        if not opts["commit"]:
            w(self.style.WARNING(
                "Report only, nothing written. Re-run with --commit to write."))
            return

        updated, created, errors = 0, 0, []
        for entry in entries:
            try:
                with transaction.atomic():  # one row must not take the file down
                    if entry["verdict"] == VERDICT_UPDATE:
                        apply_update(entry)
                        updated += 1
                    elif entry["verdict"] == VERDICT_NEW:
                        apply_insert(entry)
                        created += 1
            except Exception as exc:                    # noqa: BLE001
                errors.append((entry["file_row"], str(exc)[:200]))
                logger.exception("import_dmd_sheet row %s failed",
                                 entry["file_row"])

        w(f"updated                  {updated:>6,}")
        w(f"created                  {created:>6,}")
        w(f"errors                   {len(errors):>6,}")
        for file_row, message in errors[:20]:
            w(f"  row {file_row}, {message}")
        w("")
        w("Run map_added_users next; created rows carry no owner, so Ticket "
          "Central's per-user scope cannot see them yet.")

    def _write_csv(self, out_path, entries):
        with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "file_row", "verdict", "purpose", "link_url",
                "matched_ticket_id", "matched_ticket_number", "stored_status",
                "new_status", "candidates", "repeat_of", "changed_columns",
                "overwritten_columns",
            ])
            for entry in entries:
                ticket = entry["ticket"] or {}
                writer.writerow([
                    entry["file_row"], entry["verdict"],
                    entry["row"].get("purpose", ""),
                    entry["row"].get("link_url", ""),
                    ticket.get("id", ""), ticket.get("ticket_number", ""),
                    ticket.get("status", ""),
                    entry["payload"].get("status", ""),
                    len(entry["candidates"]), entry["repeat_of"] or "",
                    " ".join(entry["changed"]),
                    " ".join(entry["conflicts"]),
                ])
