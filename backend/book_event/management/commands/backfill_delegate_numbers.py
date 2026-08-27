"""
book_event/management/commands/backfill_delegate_numbers.py
───────────────────────────────────────────────────────────
Set BookDelegate.delegate_number from a spreadsheet of the REAL numbers.

    # 1. export what is stored right now, so the sheet is built against live rows
    python manage.py backfill_delegate_numbers --export current_numbers.csv
    python manage.py backfill_delegate_numbers --export aiu.csv --event-code "AIU - AD"

    # 2. fill in the New Delegate Number column, then match WITHOUT writing
    python manage.py backfill_delegate_numbers "path/to/file.xlsx"

    # 3. commit, once the report reads the way it should
    python manage.py backfill_delegate_numbers "path/to/file.xlsx" --apply

DRY RUN BY DEFAULT, as backfill_accounts_contact_email and
backfill_sales_executives are. Delegate Number is a column the Bookings table
shows, people filter on, and the delegate transfer path orders by, see
book_delegate/views.py and its .order_by("delegate_number", "id"), so a run
nobody meant to make must cost nothing.

THE ONE RULE THIS COMMAND EXISTS TO KEEP
Exactly one column, on exactly the rows the sheet identifies, is written.
Everything else is REPORTED and skipped. A row is written only when all four of
these hold; it resolves to exactly one stored delegate, every identifying value
the sheet carries agrees with that delegate, the new number parses as a whole
number, and the new number differs from what is stored. Anything else lands in
the report under its own heading, so "nothing was written that I did not ask
for" is a thing you can read off the output rather than a thing you have to
trust.

HOW A ROW IS MATCHED
In this order, first hit wins.
  1. An Id column, when the sheet has one, read as the delegate primary key.
     This is the strongest key available and the --export sheet carries it.
  2. Invoice Number plus Delegate Email. That pair is the model's own
     unique_together, so it identifies at most one row by construction.
  3. Invoice Number plus Name, used only where the sheet gives no email for
     that row, or where --match-name is passed. It is accepted only when it
     picks out exactly one delegate on that invoice; two delegates whose names
     collapse to the same tokens report as AMBIGUOUS and are left alone.
Invoice numbers are compared with whitespace removed and upper-cased, emails
lower-cased, names as a sorted token set, so "Inv-19251" matches "INV 19251"
and "Lovelace, Ada" matches "Ada Lovelace". The stored column is a varchar
people typed by hand, which is why the index is built in Python over a values()
read rather than as a database __in on the raw strings.

MATCHING IS VERIFIED, NOT ASSUMED
Every identifying column the sheet carries beyond the key is compared against
the stored row, and any disagreement makes the row a CONFLICT that is skipped.
That includes a stored Delegate Number column sitting alongside the new one; if
the sheet says a delegate is currently 3 and the database says 1, the sheet was
built against something other than this database and the row is not written.
Event Code is compared with its trailing edition stripped, because save() moves
that into edition and stores "AIU - AD" for a sheet that reads "AIU - AD 26".
Company is shown in the report for context and never blocks a write, since
company_name_raw is the least reliable column in the set.

WHY THE WRITE IS A QUERYSET UPDATE AND NOT save()
BookDelegate.save() rewrites event_code and edition, inherits and canonicalises
booking_code, forces delegate_count on Cancelled rows, re-derives booked_on and
fills the invoice's accounts contact email. None of that is wanted here; this is
a correction to one integer column. A grouped .update() per distinct value
writes that column and nothing else, in one statement per value.

WHY updated_at IS LEFT ALONE BY DEFAULT
BookDelegateViewSet.ordering is ["-updated_at", "-id"], so updated_at is the
Bookings table's default sort. book_delegate/services.py sets it by hand on its
queryset .update() precisely because clearing overrides is a real edit somebody
made. A bulk backfill is not; stamping several thousand rows would push the
whole backfill above every genuine edit and destroy the signal that ordering
exists for. Pass --touch-updated-at if you want these rows to surface at the top
of the table anyway.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.import_common import as_int, as_text, clean_header, read_import_rows
from book_delegate.models import BookDelegate

# ── Header aliases ───────────────────────────────────────────────────────────
# Role to accepted header spellings, cleaned by clean_header, in PRIORITY order.
# The roles themselves are resolved in the order written here, which is why
# "number" is settled before "name"; a sheet whose only name column is headed
# "Delegate" must not have it claimed as the number column.
#
# "new delegate number" leads the number aliases so that a sheet carrying BOTH
# the stored value and the corrected one writes the corrected one. The stored
# column is then picked up as "current_number" below and VERIFIED.
_ALIASES = {
    "id": ("id", "delegate id", "delegate_id", "record id", "row id", "pk"),
    "invoice": ("invoice number", "invoice no", "invoice no.", "invoice #",
                "invoice", "invoice_number", "inv", "inv no"),
    "email": ("delegate email", "email", "email address", "e-mail",
              "delegate email address", "delegate_email"),
    "number": ("new delegate number", "real delegate number",
               "correct delegate number", "corrected delegate number",
               "actual delegate number", "delegate number", "delegate no",
               "delegate no.", "delegate_number", "number"),
    "name": ("name", "delegate name", "full name", "delegate", "attendee",
             "attendee name"),
    "first_name": ("first name", "firstname", "first_name", "given name"),
    "last_name": ("last name", "lastname", "last_name", "surname",
                  "family name"),
    "event_code": ("event code", "event_code", "event"),
    "edition": ("edition",),
    "company": ("delegate company", "company", "company name", "company_name",
                "company (raw)"),
}

# Columns a sheet is welcome to carry for the reader's benefit and that this
# command neither matches on nor verifies. They are consumed silently, so the
# unrecognised-column warning stays meaningful; without this the template
# --export writes would warn about its own Payment Status column, and a warning
# that fires on correct input teaches people to ignore warnings.
#
# Payment status is here rather than verified because delegate_payment_status is
# NULL on any delegate that inherits the invoice's status, which is most of
# them, so comparing it would flag correctly matched rows by the thousand.
#
# The list is the union of the headers the Zoho Event Bookings Report and the
# master Google Sheet export actually carry, so a straight export of either runs
# without a single spurious warning.
_INFORMATIONAL = frozenset((
    "payment status", "delegate payment status", "paid/free", "payable/free",
    "paid or free", "payment type", "payment date", "date paid", "ticket tier",
    "ticket package", "attendance", "attendance - in?", "position",
    "job title", "phone", "direct line", "phone number", "notes", "reference",
    "ref", "booking code", "sales executive", "team leader", "added time",
    "modified time", "created at", "updated at", "booked on", "delegate count",
    "request date", "invoice date", "event date", "event name", "discount",
    "add-ons", "add ons", "accounts contact", "accounts contact email",
    "currency", "sponsorship level", "dietary requirements",
))

# Every field the matcher and the report need, and not one more. A values() read
# rather than model instances, because the whole table is loaded and 14,800
# hydrated BookDelegate objects to compare five strings would be waste.
_DB_FIELDS = ("id", "invoice_id", "email", "first_name", "last_name",
              "event_code", "edition", "company_name_raw", "delegate_number",
              "delegate_payment_status")

# Report headings, in the order they are written to the markdown file. WRITE and
# UNCHANGED are the two outcomes that mean the sheet and the database agree
# about which row is which; everything below them is something to look at.
WRITE = "WRITE"
UNCHANGED = "UNCHANGED"
BLANK = "BLANK"
DUPLICATE_SAME = "DUPLICATE_SAME"
BAD_NUMBER = "BAD_NUMBER"
NO_KEY = "NO_KEY"
INVOICE_NOT_FOUND = "INVOICE_NOT_FOUND"
DELEGATE_NOT_FOUND = "DELEGATE_NOT_FOUND"
AMBIGUOUS = "AMBIGUOUS"
CONFLICT = "CONFLICT"
DUPLICATE_CLASH = "DUPLICATE_CLASH"

_ORDER = (WRITE, CONFLICT, DUPLICATE_CLASH, AMBIGUOUS, DELEGATE_NOT_FOUND,
          INVOICE_NOT_FOUND, BAD_NUMBER, NO_KEY, DUPLICATE_SAME, BLANK,
          UNCHANGED)

_DESCRIPTIONS = {
    WRITE: "Matched, verified, and the number changes. These are written by --apply.",
    CONFLICT: "Matched one delegate, but a value in the sheet disagrees with the stored row. NOT written.",
    DUPLICATE_CLASH: "Two or more sheet rows resolve to the same delegate with DIFFERENT numbers. NOT written.",
    AMBIGUOUS: "The key in the sheet fits more than one stored delegate. NOT written.",
    DELEGATE_NOT_FOUND: "The invoice exists, but no delegate on it matches this row. NOT written.",
    INVOICE_NOT_FOUND: "No delegate is stored against this invoice number at all. NOT written.",
    BAD_NUMBER: "The new number is not a whole number, or is below the minimum. NOT written.",
    NO_KEY: "The row carries nothing to match on. NOT written.",
    DUPLICATE_SAME: "A later row repeating an earlier one with the SAME number. Written once, from the first row.",
    BLANK: "No new number given, so the stored value is deliberately left alone.",
    UNCHANGED: "Matched and verified; the stored number is already the one in the sheet.",
}


def _norm_text(value):
    """Trimmed, whitespace-collapsed, lower-cased. The general comparison key."""
    return " ".join(as_text(value).split()).lower()


def _norm_invoice(value):
    """
    Invoice numbers with ALL whitespace removed and upper-cased.

    Whitespace is removed rather than collapsed, so "INV 19251", "Inv-19251"
    and "inv19251" do not all have to be spelled in the sheet exactly the way
    they happen to be spelled in the database. Separators are kept, so INV-1
    and INV1 stay distinguishable, which is how they are already stored.
    """
    return re.sub(r"\s+", "", as_text(value)).upper()


def _norm_email(value):
    return as_text(value).strip().lower()


def _norm_event_code(value):
    """
    An event code with its trailing edition removed, punctuation dropped.

    BookDelegate.save() pulls the trailing 2 to 4 digits off event_code into
    edition, so the stored value for a sheet reading "AIU - AD 26" is
    "AIU - AD". The same strip is applied to both sides here, so an event code
    in the sheet is a usable check rather than a guaranteed disagreement.
    """
    text = re.sub(r"\s*-?\s*\d{2,4}$", "", _norm_text(value)).strip()
    return re.sub(r"[^a-z0-9]+", "", text)


def _name_tokens(*parts):
    """
    Name parts as a sorted tuple of alphanumeric tokens.

    Sorted, so "Lovelace, Ada" and "Ada Lovelace" are the same key; punctuation
    stripped, so "O'Brien" and "OBrien" are too. Order is discarded on purpose,
    because the sheet's single Name column and the stored first_name/last_name
    pair do not agree on it.
    """
    text = " ".join(as_text(p) for p in parts).lower()
    return tuple(sorted(t for t in re.sub(r"[^a-z0-9]+", " ", text).split() if t))


def _names_agree(sheet, stored):
    """
    True when two token sets describe the same person as far as this can tell.

    Equality, or either set contained in the other. The containment arm is what
    lets a sheet holding "Ada Lovelace" verify against a stored "Ada Marie
    Lovelace", and a sheet holding only a first name verify against the full
    name. An empty sheet name verifies nothing and therefore agrees.
    """
    if not sheet or not stored:
        return True
    a, b = set(sheet), set(stored)
    return a == b or a <= b or b <= a


class Command(BaseCommand):
    help = (
        "Write BookDelegate.delegate_number from a spreadsheet of real numbers, "
        "matching each row against the stored record first and touching no "
        "other row and no other column. Dry-run unless --apply is given."
    )

    # ── Arguments ────────────────────────────────────────────────────────────
    def add_arguments(self, parser):
        parser.add_argument(
            "workbook", nargs="?",
            help="Path to the .xlsx/.xlsm/.csv holding the real delegate numbers.",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Commit the updates. Without this flag, nothing is written.",
        )
        parser.add_argument(
            "--export", metavar="PATH",
            help=("Write the stored delegates to a CSV template instead of "
                  "importing, with an empty New Delegate Number column to fill "
                  "in."),
        )
        parser.add_argument(
            "--event-code", metavar="CODE",
            help=("Restrict --export to one event code, compared with its "
                  "trailing edition stripped."),
        )
        parser.add_argument(
            "--number-column", metavar="HEADER",
            help=("Name the column holding the new numbers, for a sheet whose "
                  "header this command would not recognise or would read as "
                  "something else. Matched case-insensitively on the trimmed "
                  "header. The named column is used for nothing else."),
        )
        parser.add_argument(
            "--match-name", action="store_true",
            help=("Fall back to Invoice Number plus Name even when the row DOES "
                  "carry an email that matched nothing. Off by default, because "
                  "an email that does not match is a sign the sheet and the "
                  "database disagree about the row."),
        )
        parser.add_argument(
            "--ignore-conflicts", action="store_true",
            help=("Write rows whose non-key values disagree with the stored "
                  "record. The disagreements are still reported."),
        )
        parser.add_argument(
            "--min-number", type=int, default=1,
            help=("Lowest accepted delegate number, default 1. Pass 0 to allow "
                  "a zero, or a negative value to allow anything."),
        )
        parser.add_argument(
            "--touch-updated-at", action="store_true",
            help=("Stamp updated_at on every written row. Off by default so a "
                  "backfill does not take over the Bookings table's default "
                  "sort; see the module docstring."),
        )
        parser.add_argument(
            "--report", metavar="PATH",
            help=("Where to write the markdown report, default "
                  "delegate_number_issues.md in the repo root."),
        )
        parser.add_argument(
            "--batch-size", type=int, default=500,
            help="Primary keys per UPDATE statement, default 500.",
        )

    # ── Entry point ──────────────────────────────────────────────────────────
    def handle(self, *args, **options):
        if options["export"]:
            return self._export(options)

        workbook = options["workbook"]
        if not workbook:
            raise CommandError(
                "Give the path to the spreadsheet, or use --export PATH to "
                "produce a template from the stored rows first."
            )
        path = Path(workbook).expanduser()
        if not path.exists():
            raise CommandError(f"File not found, {path}")

        rows = read_import_rows(str(path))
        if not rows:
            raise CommandError(f"No data rows found in {path.name}")

        columns = []
        for row in rows:
            for col in row:
                if col not in columns:
                    columns.append(col)
        roles, unrecognised = self._resolve_columns(
            columns, override=options["number_column"], path=path,
        )
        self._require_columns(roles, path)

        index = self._load_index()
        entries = [
            self._classify(row, number, roles, index, options)
            for number, row in enumerate(rows, start=2)
        ]
        self._resolve_duplicates(entries)

        writable = [e for e in entries if e["outcome"] == WRITE]
        clashes = self._post_state_clashes(writable, index)
        report_path = self._write_report(
            path, report=options["report"], entries=entries, roles=roles,
            unrecognised=unrecognised, clashes=clashes, applied=options["apply"],
        )

        written = 0
        if options["apply"] and writable:
            written = self._write(writable, options)

        self._summarise(entries, unrecognised, clashes, report_path,
                        applied=options["apply"], written=written)

    # ── Columns ──────────────────────────────────────────────────────────────
    def _resolve_columns(self, columns, override=None, path=None):
        """
        The sheet's own header labels, mapped to the roles this command needs.

        Unrecognised columns are REPORTED rather than dropped in silence, the
        same reasoning as accounts.import_common.build_header_mapper; a mistyped
        header would otherwise present as a column of empty data, and here that
        would read as "nothing to update" rather than as a mistake.

        --number-column, when given, CLAIMS its column before anything else runs
        and is exact. A sheet can label the delegate number in a way no alias
        list should be taught to guess at; the master Google Sheet export heads
        it "Delegate Count", which is also the name of a DIFFERENT field on this
        model, so guessing there would be a write to the wrong column dressed up
        as a convenience.
        """
        cleaned = {col: clean_header(col) for col in columns}
        roles, used = {}, set()

        if override:
            wanted = clean_header(override)
            hit = next((c for c in columns if cleaned[c] == wanted), None)
            if hit is None:
                raise CommandError(
                    f"--number-column {override!r} is not a column in "
                    f"{path.name if path else 'the sheet'}. Its columns are "
                    f"{', '.join(repr(str(c)) for c in columns)}."
                )
            roles["number"] = hit
            used.add(hit)

        for role, aliases in _ALIASES.items():
            if role in roles:
                continue
            for alias in aliases:
                hit = next(
                    (c for c in columns if cleaned[c] == alias and c not in used),
                    None,
                )
                if hit:
                    roles[role] = hit
                    used.add(hit)
                    break
        # A sheet carrying the stored number AND the corrected one. The number
        # role above took the corrected column; whichever number-ish column is
        # left is the stored value, which is verified and never written.
        leftover = [c for c in columns
                    if c not in used and cleaned[c] in _ALIASES["number"]]
        if leftover:
            roles["current_number"] = leftover[0]
            used.add(leftover[0])
        return roles, [c for c in columns
                       if c not in used and cleaned[c] not in _INFORMATIONAL]

    def _require_columns(self, roles, path):
        if "number" not in roles:
            raise CommandError(
                f"{path.name} has no delegate number column. Head it "
                f"\"New Delegate Number\", or any of "
                f"{', '.join(_ALIASES['number'])}."
            )
        if "id" in roles:
            return
        if "invoice" not in roles:
            raise CommandError(
                f"{path.name} has no Id column and no Invoice Number column, so "
                f"there is nothing to match a row on."
            )
        if not ({"email", "name", "first_name", "last_name"} & set(roles)):
            raise CommandError(
                f"{path.name} has Invoice Number but no Delegate Email and no "
                f"Name, and an invoice on its own does not identify a delegate."
            )

    # ── The stored side ──────────────────────────────────────────────────────
    def _load_index(self):
        """
        Every stored delegate, indexed three ways for the three match keys.

        The whole table is read, not a filtered slice. invoice_number is a
        varchar that people typed, so the normalised key the sheet is matched on
        cannot be expressed as a database __in over the raw strings; and at the
        current size this is a single sequential read of ten columns.
        """
        index = {
            "by_id": {},
            "by_invoice_email": defaultdict(list),
            "by_invoice_name": defaultdict(list),
            "invoices": set(),
        }
        for row in BookDelegate.objects.all().values(*_DB_FIELDS).iterator():
            invoice = _norm_invoice(row["invoice_id"])
            row["_invoice_key"] = invoice
            row["_tokens"] = _name_tokens(row["first_name"], row["last_name"])
            index["by_id"][row["id"]] = row
            index["invoices"].add(invoice)
            index["by_invoice_email"][(invoice, _norm_email(row["email"]))].append(row)
            index["by_invoice_name"][(invoice, row["_tokens"])].append(row)
        return index

    # ── One sheet row ────────────────────────────────────────────────────────
    def _classify(self, row, number, roles, index, options):
        """
        One sheet row to one report entry. Never writes, never raises.

        Matching runs BEFORE the new number is looked at, so a row that matched
        cleanly but was left blank reports as BLANK against a named delegate
        rather than as an anonymous empty row.
        """
        def get(role):
            return row.get(roles[role]) if role in roles else None

        entry = {
            "row": number,
            "outcome": None,
            "detail": "",
            "delegate": None,
            "value": None,
            "sheet": {
                "invoice": as_text(get("invoice")),
                "email": as_text(get("email")),
                "name": as_text(get("name")) or " ".join(
                    p for p in (as_text(get("first_name")),
                                as_text(get("last_name"))) if p
                ),
                "event_code": as_text(get("event_code")),
                "company": as_text(get("company")),
                "number": as_text(get("number")),
            },
        }

        delegate, failure, detail = self._match(get, roles, index, options)
        if delegate is None:
            entry["outcome"], entry["detail"] = failure, detail
            return entry
        entry["delegate"] = delegate

        disagreements = self._verify(get, roles, delegate)
        if disagreements and not options["ignore_conflicts"]:
            entry["outcome"] = CONFLICT
            entry["detail"] = "; ".join(disagreements)
            return entry
        if disagreements:
            entry["detail"] = "forced past " + "; ".join(disagreements)

        value, error = as_int(get("number"))
        if error:
            entry["outcome"] = BAD_NUMBER
            entry["detail"] = error
            return entry
        if value is None:
            entry["outcome"] = BLANK
            return entry
        if value < options["min_number"]:
            entry["outcome"] = BAD_NUMBER
            entry["detail"] = (
                f"{value} is below the minimum of {options['min_number']}"
            )
            return entry

        entry["value"] = value
        entry["outcome"] = UNCHANGED if value == delegate["delegate_number"] else WRITE
        return entry

    def _match(self, get, roles, index, options):
        """The stored delegate this row means, or why there is not exactly one."""
        if "id" in roles and as_text(get("id")):
            pk, error = as_int(get("id"))
            if error or pk is None:
                return None, NO_KEY, f"Id {as_text(get('id'))!r} is not a whole number"
            delegate = index["by_id"].get(pk)
            if delegate is None:
                return None, DELEGATE_NOT_FOUND, f"no delegate has id {pk}"
            return delegate, None, ""

        invoice = _norm_invoice(get("invoice"))
        if not invoice:
            return None, NO_KEY, "no Id and no Invoice Number on this row"
        if invoice not in index["invoices"]:
            return None, INVOICE_NOT_FOUND, f"no delegate is stored against {invoice}"

        email = _norm_email(get("email"))
        if email:
            candidates = index["by_invoice_email"].get((invoice, email), [])
            if len(candidates) == 1:
                return candidates[0], None, ""
            if len(candidates) > 1:
                return None, AMBIGUOUS, (
                    f"{len(candidates)} delegates on {invoice} share the email "
                    f"{email}, ids {', '.join(str(c['id']) for c in candidates)}"
                )
            if not options["match_name"]:
                return None, DELEGATE_NOT_FOUND, (
                    f"{email} is not on {invoice}; pass --match-name to fall "
                    f"back to the name"
                )

        tokens = _name_tokens(get("name"), get("first_name"), get("last_name"))
        if not tokens:
            return None, DELEGATE_NOT_FOUND, (
                f"nothing on {invoice} matches, and this row gives no name to "
                f"fall back to"
            )
        candidates = index["by_invoice_name"].get((invoice, tokens), [])
        if len(candidates) == 1:
            return candidates[0], None, ""
        if len(candidates) > 1:
            return None, AMBIGUOUS, (
                f"{len(candidates)} delegates on {invoice} are named "
                f"{' '.join(tokens)}, ids "
                f"{', '.join(str(c['id']) for c in candidates)}"
            )
        return None, DELEGATE_NOT_FOUND, (
            f"no delegate on {invoice} is named {' '.join(tokens)}"
        )

    def _verify(self, get, roles, delegate):
        """
        Every disagreement between this sheet row and the stored delegate.

        A column the sheet does not carry, or leaves blank on this row, verifies
        nothing and is skipped. Company is deliberately absent; company_name_raw
        is free text off an import and would flag rows that are otherwise
        perfectly matched.
        """
        problems = []
        email = _norm_email(get("email"))
        if email and email != _norm_email(delegate["email"]):
            problems.append(
                f"email {email} vs stored {_norm_email(delegate['email'])}"
            )

        tokens = _name_tokens(get("name"), get("first_name"), get("last_name"))
        if tokens and not _names_agree(tokens, delegate["_tokens"]):
            problems.append(
                f"name {' '.join(tokens)} vs stored {' '.join(delegate['_tokens'])}"
            )

        if "invoice" in roles:
            invoice = _norm_invoice(get("invoice"))
            if invoice and invoice != delegate["_invoice_key"]:
                problems.append(
                    f"invoice {invoice} vs stored {delegate['_invoice_key']}"
                )

        sheet_code = _norm_event_code(get("event_code"))
        if sheet_code and sheet_code != _norm_event_code(delegate["event_code"]):
            problems.append(
                f"event code {as_text(get('event_code'))} vs stored "
                f"{delegate['event_code']}"
            )

        # Edition is compared as a number, not as text, so a cell Excel decided
        # was 2026.0 still reads as 2026. A blank on either side verifies
        # nothing, because edition is nullable and plenty of rows carry no year.
        sheet_edition, edition_error = as_int(get("edition"))
        if (not edition_error and sheet_edition is not None
                and delegate["edition"] is not None
                and sheet_edition != delegate["edition"]):
            problems.append(
                f"edition {sheet_edition} vs stored {delegate['edition']}"
            )

        if "current_number" in roles:
            stored_claim, error = as_int(get("current_number"))
            if (not error and stored_claim is not None
                    and stored_claim != delegate["delegate_number"]):
                problems.append(
                    f"sheet says the current number is {stored_claim}, stored "
                    f"is {delegate['delegate_number']}"
                )
        return problems

    # ── Cross-row checks ─────────────────────────────────────────────────────
    def _resolve_duplicates(self, entries):
        """
        Two sheet rows aiming at one delegate.

        Same number, the later rows become DUPLICATE_SAME and the first still
        writes; different numbers, the sheet contradicts itself about that
        delegate and EVERY row involved is demoted to DUPLICATE_CLASH. Choosing
        one of two contradictory numbers is not this command's call to make.
        """
        by_pk = defaultdict(list)
        for entry in entries:
            if entry["outcome"] in (WRITE, UNCHANGED) and entry["delegate"]:
                by_pk[entry["delegate"]["id"]].append(entry)

        for pk, group in by_pk.items():
            if len(group) == 1:
                continue
            if len({e["value"] for e in group}) > 1:
                listed = ", ".join(f"row {e['row']} says {e['value']}" for e in group)
                for entry in group:
                    entry["outcome"] = DUPLICATE_CLASH
                    entry["detail"] = f"delegate {pk}, {listed}"
                continue
            for entry in group[1:]:
                if entry["outcome"] == WRITE:
                    entry["outcome"] = DUPLICATE_SAME
                    entry["detail"] = (
                        f"delegate {pk} is already being set to {entry['value']} "
                        f"by row {group[0]['row']}"
                    )

    def _post_state_clashes(self, writable, index):
        """
        Invoices left with two delegates sharing a number once this is applied.

        A WARNING and never a block. delegate_number carries no uniqueness
        constraint and this command is not the place to invent one; but the
        transfer path orders by it, so a sheet that accidentally gives two
        delegates on one invoice the same number is worth seeing before --apply
        rather than after.
        """
        planned = {e["delegate"]["id"]: e["value"] for e in writable}
        touched = {e["delegate"]["_invoice_key"] for e in writable}
        per_invoice = defaultdict(lambda: defaultdict(list))
        for delegate in index["by_id"].values():
            key = delegate["_invoice_key"]
            if key not in touched:
                continue
            number = planned.get(delegate["id"], delegate["delegate_number"])
            per_invoice[key][number].append(delegate["id"])

        clashes = []
        for invoice in sorted(per_invoice):
            for number in sorted(per_invoice[invoice]):
                ids = per_invoice[invoice][number]
                if len(ids) > 1:
                    clashes.append((invoice, number, ids))
        return clashes

    # ── The write ────────────────────────────────────────────────────────────
    def _write(self, writable, options):
        """
        One UPDATE per distinct number, batched by primary key.

        Grouped rather than row by row, so a thousand corrections are a handful
        of statements. filter(pk__in=...) is an integer primary key filter, not
        the varchar FK filter the rest of this app has to work with, so there is
        no room for it to match more than the listed rows.
        """
        groups = defaultdict(list)
        for entry in writable:
            groups[entry["value"]].append(entry["delegate"]["id"])

        extra = {}
        if options["touch_updated_at"]:
            # timezone.now() rather than a literal, so it is tz-aware under
            # USE_TZ and stored as UTC like every other timestamp here. A
            # queryset .update() does NOT fire auto_now, which is why this has
            # to be explicit; see book_delegate/services.py.
            extra["updated_at"] = timezone.now()

        size = max(1, options["batch_size"])
        written = 0
        # One transaction, as backfill_accounts_contact_email uses; an
        # interrupted run leaves the numbers either fully corrected or entirely
        # untouched, never half corrected from whichever batch it reached, which
        # afterwards is indistinguishable from a sheet that was half filled in.
        with transaction.atomic():
            for value in sorted(groups):
                pks = groups[value]
                for start in range(0, len(pks), size):
                    written += (
                        BookDelegate.objects
                        .filter(pk__in=pks[start:start + size])
                        .update(delegate_number=value, **extra)
                    )
        return written

    # ── Output ───────────────────────────────────────────────────────────────
    def _write_report(self, source, report, entries, roles, unrecognised,
                      clashes, applied):
        """
        The full row-by-row account, written on a dry run as well as on --apply.

        Alongside import_issues.md in the repo root by default, for the same
        reason that file is there; the terminal shows counts, the file shows
        which rows they were.
        """
        path = (Path(report).expanduser() if report else
                Path(__file__).resolve().parents[4] / "delegate_number_issues.md")
        buckets = defaultdict(list)
        for entry in entries:
            buckets[entry["outcome"]].append(entry)

        lines = [
            "# Delegate Number backfill",
            "",
            f"- Source, `{source}`",
            f"- Mode, {'APPLIED' if applied else 'dry run, nothing written'}",
            f"- Rows read, {len(entries)}",
            "",
            "## Columns",
            "",
        ]
        for role in ("id", "invoice", "email", "name", "first_name",
                     "last_name", "event_code", "edition", "company",
                     "current_number", "number"):
            if role in roles:
                used = "verified"
                if role in ("id", "invoice"):
                    used = "matched on"
                elif role == "company":
                    used = "shown for context, never verified"
                elif role == "number":
                    used = "written"
                lines.append(f"- `{roles[role]}` read as {role}, {used}")
        if unrecognised:
            lines += ["", "Columns in the sheet that this command ignores.", ""]
            lines += [f"- `{col}`" for col in unrecognised]

        lines += ["", "## Counts", ""]
        for outcome in _ORDER:
            lines.append(f"- {outcome}, {len(buckets.get(outcome, []))}")

        if clashes:
            lines += [
                "", "## Warning, duplicate numbers within an invoice", "",
                "These are not blocked and are written. After this run the "
                "listed invoices carry the same delegate number twice.", "",
                "| Invoice | Delegate Number | Delegate ids |",
                "| --- | --- | --- |",
            ]
            for invoice, number, ids in clashes:
                lines.append(
                    f"| {invoice} | {number} | {', '.join(str(i) for i in ids)} |"
                )

        for outcome in _ORDER:
            rows = buckets.get(outcome, [])
            if not rows:
                continue
            lines += [
                "", f"## {outcome}, {len(rows)} row(s)", "",
                _DESCRIPTIONS[outcome], "",
                "| Sheet row | Delegate id | Invoice | Name | Email | Stored | Sheet | Note |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
            for entry in rows:
                delegate = entry["delegate"]
                cells = (
                    str(entry["row"]),
                    str(delegate["id"]) if delegate else "",
                    delegate["_invoice_key"] if delegate else entry["sheet"]["invoice"],
                    (f"{delegate['first_name']} {delegate['last_name']}".strip()
                     if delegate else entry["sheet"]["name"]),
                    delegate["email"] if delegate else entry["sheet"]["email"],
                    str(delegate["delegate_number"]) if delegate else "",
                    entry["sheet"]["number"],
                    entry["detail"].replace("|", "/"),
                )
                lines.append("| " + " | ".join(cells) + " |")

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _summarise(self, entries, unrecognised, clashes, report_path, applied,
                   written):
        buckets = defaultdict(int)
        for entry in entries:
            buckets[entry["outcome"]] += 1

        self.stdout.write(f"Rows read              : {len(entries)}")
        for outcome in _ORDER:
            self.stdout.write(f"  {outcome:<20}: {buckets[outcome]}")
        if unrecognised:
            self.stdout.write(self.style.WARNING(
                f"Ignored {len(unrecognised)} unrecognised column(s), "
                f"{', '.join(str(c) for c in unrecognised)}"
            ))
        if clashes:
            self.stdout.write(self.style.WARNING(
                f"{len(clashes)} invoice/number pair(s) end up used twice; see "
                f"the report."
            ))
        self.stdout.write(f"Report                 : {report_path}")

        blockers = sum(buckets[o] for o in (
            CONFLICT, DUPLICATE_CLASH, AMBIGUOUS, DELEGATE_NOT_FOUND,
            INVOICE_NOT_FOUND, BAD_NUMBER, NO_KEY,
        ))
        if applied:
            self.stdout.write(self.style.SUCCESS(
                f"Updated delegate_number on {written} delegate(s). No other "
                f"column and no other row was written."
            ))
            if blockers:
                self.stdout.write(self.style.WARNING(
                    f"{blockers} row(s) were skipped and are still unfixed; see "
                    f"the report."
                ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Dry run, nothing written. {buckets[WRITE]} row(s) would "
                f"change. Re-run with --apply."
            ))

    # ── Template export ──────────────────────────────────────────────────────
    def _export(self, options):
        """
        The stored rows as a CSV to fill in, so the sheet is built on real keys.

        Id is included and is what the import matches on first, which removes
        every ambiguity the name and email keys can run into. New Delegate
        Number is left empty; a blank means "leave this delegate alone", so the
        exported file re-imports as a no-op until somebody types in it.
        """
        path = Path(options["export"]).expanduser()
        wanted = (_norm_event_code(options["event_code"])
                  if options["event_code"] else None)

        headers = ("Id", "Invoice Number", "Event Code", "Edition", "Name",
                   "Delegate Email", "Delegate Company", "Payment Status",
                   "Delegate Number", "New Delegate Number")
        rows = (BookDelegate.objects.all().values(*_DB_FIELDS)
                .order_by("invoice_id", "delegate_number", "id"))
        count = 0
        # utf-8-sig, so Excel opens the file with the accented company names
        # intact rather than as mojibake; _read_csv_rows reads the BOM back off.
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            for row in rows.iterator():
                if wanted and _norm_event_code(row["event_code"]) != wanted:
                    continue
                writer.writerow((
                    row["id"], row["invoice_id"], row["event_code"],
                    row["edition"] or "",
                    f"{row['first_name']} {row['last_name']}".strip(),
                    row["email"], row["company_name_raw"],
                    row["delegate_payment_status"] or "",
                    row["delegate_number"], "",
                ))
                count += 1

        self.stdout.write(self.style.SUCCESS(f"Wrote {count} row(s) to {path}"))
        self.stdout.write(
            "Fill in New Delegate Number, leave a row blank to skip it, then "
            "run the same command with the file path to see the match report."
        )
