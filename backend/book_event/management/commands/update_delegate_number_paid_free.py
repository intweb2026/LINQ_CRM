"""
management command, update_delegate_number_paid_free

Updates exactly two stored columns from an Excel workbook, and nothing else.

    Delegate Number  ->  BookDelegate.delegate_number
    Paid/Free        ->  the Payable / Free value the CRM displays, which lives
                         in BookEvent.paid_or_free and in the per-delegate
                         override BookDelegate.delegate_paid_or_free together.

A workbook row is matched onto a stored delegate on the pair Invoice Number
plus Name; nothing else is used to match, and nothing else is written.

PAYABLE / FREE IS TWO COLUMNS, NOT ONE
Every serializer resolves it as `delegate_paid_or_free or invoice.paid_or_free`,
so writing one of the two and not the other produces a booking that answers the
question differently depending on who asks. That is the failure this command is
built to avoid, and it is why --paid-free-target defaults to sync.

  sync      the invoice carries the value the delegates agree on, and an
            override survives only where one delegate genuinely differs. This is
            the rule frontend/src/api/bookings.js states and the booking modal
            follows, so a workbook update and a hand edit leave the same shape.
  delegate  the override only. The Bookings table reads right and
            invoice.paid_or_free keeps the old value, which BookEventFilter, the
            read-only `paid_or_free` serializer field, the sync export and the
            parent half of the bulk-update spec all still read.
  invoice   the invoice only. A delegate carrying an override keeps displaying
            the old value, so those rows are reported instead of being left
            quietly wrong.

Under sync, an invoice is moved only when the workbook accounts for EVERY
delegate on it. Where it covers only some of them, what the invoice should say
is not knowable from the file, and moving it would re-label the people the file
never mentioned, so the invoice is left alone and the covered rows are carried
as per-delegate differences instead. Those invoices are counted in the summary,
so a workbook that is a partial extract does not look like a silent no-op.

WHY THIS EXISTS
The earlier importers did not land these two columns where the CRM reads them.

  1. import_remaining_bookings defaults to --delegate-number-as delegate_count,
     so the workbook's 0/1 "Delegate Number" column was stored in
     BookDelegate.delegate_count. The Bookings table and its filters read
     BookDelegate.delegate_number instead, and DelegateTable.jsx dropped
     delegate_count from the UI on purpose, so a row imported that way displays
     the model default of 1 whatever the workbook said. Some of that has since
     been corrected by hand and by `backfill_delegate_numbers`, which is why
     this command reports what already agrees rather than assuming nothing does.
  2. Paid/Free was written to the invoice, and to the per-delegate override only
     on the rows that disagreed with their invoice, under a fill-blanks policy
     that could not correct a value already stored. A workbook that is
     delegate-grained on this column could not be reproduced that way.

SAFETY
  - Dry run is the DEFAULT. Pass --apply to write.
  - Writes go through bulk_update, so BookDelegate.save() never runs and cannot
    touch event_code, booking_code, booked_on, delegate_count or the accounts
    contact as a side effect.
  - A blank cell is skipped, never written, unless --allow-clear is passed.
  - updated_at is left alone unless --touch-updated-at is passed, so a bulk
    correction does not reshuffle a table sorted on last modified.

RELATED
`backfill_delegate_numbers` corrects delegate_number alone, from a hand-built
correction sheet, and verifies every identifying column the sheet carries before
it writes. Use that one when the sheet is a correction list somebody typed; use
this one when the file is a full export and both columns are to be reconciled.

Usage
    python manage.py update_delegate_number_paid_free
    python manage.py update_delegate_number_paid_free "data_imports/remaining_data.xlsx"
    python manage.py update_delegate_number_paid_free --report fix_report.md
    python manage.py update_delegate_number_paid_free --apply
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

import openpyxl
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from book_delegate.models import BookDelegate
from book_event.models import BookEvent

DEFAULT_WORKBOOK = "data_imports/remaining_data.xlsx"

# Header spellings accepted for each column this command needs. Compared after
# lowercasing and collapsing whitespace, so "Delegate  Number" matches too.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "invoice": ("invoice number", "invoice no", "invoice no.", "invoice #",
                "invoice_number", "invoice"),
    "name": ("name", "delegate name", "full name", "delegate"),
    # "delegate count" is in this list on purpose. The Master Data export spells
    # the 0/1 flag that way, and that flag is what the Bookings table shows as
    # Delegate Number; DelegateTable.jsx dropped the model's own delegate_count
    # from the UI. Whichever header matched is printed at the start of every run,
    # so this can never be a silent guess.
    "delegate_number": ("delegate number", "delegate no", "delegate no.",
                        "delegate #", "delegate_number", "delegate num",
                        "delegate count"),
    "paid_or_free": ("paid/free", "paid / free", "payable/free", "payable / free",
                     "payable free", "paid or free", "paid_or_free", "paid free"),
    "email": ("delegate email", "email", "email address", "delegate_email"),
}

# Stored spelling for every value this column may hold, keyed by a lowered cell.
PAID_OR_FREE_LOOKUP = {v.lower(): v for v in BookEvent.PaidOrFree.values}
PAID_OR_FREE_LOOKUP.update({
    "payable": "Paid",
    "pay": "Paid",
    "complimentary": "Free",
    "comp": "Free",
    "free of charge": "Free",
    "foc": "Free",
})


def _cell(v) -> str:
    """Any cell, as a stripped single-spaced string; '' when blank."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    if s.lower() in ("nan", "none", "nat", "null"):
        return ""
    return " ".join(s.split())


def _header_key(v) -> str:
    return " ".join(str(v or "").strip().lower().split())


def _invoice_key(v) -> str:
    """
    An invoice number reduced to what identifies it.

    Case and surrounding space differ between the workbook and the database on
    some rows, so both sides are folded the same way before being compared.
    """
    return " ".join(str(v or "").strip().upper().split())


def _name_key(v) -> str:
    """
    A full name reduced to what identifies the person.

    Punctuation is dropped, so "Nuno G. Rodrigues" and "Nuno G Rodrigues" are
    one key; case is folded, so "MCDONALD" and "McDonald" are one key.
    """
    s = re.sub(r"[^0-9a-z]+", " ", str(v or "").lower())
    return " ".join(s.split())


def _paid_or_free(raw: str) -> tuple[Optional[str], bool]:
    """
    A Paid/Free cell as the stored spelling, plus whether it was readable.

    Returns (None, True) for a blank cell, and (None, False) for a cell holding
    something this column cannot mean.
    """
    if not raw:
        return None, True
    key = " ".join(raw.strip().lower().split())
    if key in ("-", "n/a", "na"):
        return None, True
    value = PAID_OR_FREE_LOOKUP.get(key)
    if value is None:
        return None, False
    return value, True


def _delegate_number(raw: str) -> tuple[Optional[int], bool]:
    """
    A Delegate Number cell as an int, plus whether it was readable.

    The column holds 0 or 1; a float spelling such as "1.0" is accepted, because
    that is how a numeric Excel cell reaches a string reader.
    """
    if not raw:
        return None, True
    try:
        return int(float(raw)), True
    except (TypeError, ValueError):
        return None, False


class Command(BaseCommand):
    help = (
        "Update BookDelegate.delegate_number and the Payable / Free column from "
        "an Excel workbook, matching rows on Invoice Number plus Name. No other "
        "field is written. Dry run unless --apply is passed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "excel_path", nargs="?", default=None,
            help=f"Path to the workbook; defaults to {DEFAULT_WORKBOOK}.",
        )
        parser.add_argument(
            "--sheet", default=None,
            help="Worksheet name; defaults to the first sheet.",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the changes. Without it the command only reports.",
        )
        parser.add_argument(
            "--fields", choices=("both", "delegate-number", "paid-free"),
            default="both",
            help="Which of the two columns to write.",
        )
        parser.add_argument(
            "--paid-free-target", choices=("sync", "delegate", "invoice"),
            default="sync",
            help=(
                "sync, the default, puts the value on the INVOICE and clears the "
                "per-delegate override wherever the delegates agree, keeping an "
                "override only to carry a genuine per-delegate difference; this "
                "is the rule the CRM's own booking modal follows, and it is the "
                "only one that leaves the invoice column and the displayed value "
                "telling the same story. delegate writes the override alone and "
                "leaves the invoice stale. invoice writes the invoice alone and "
                "reports any override that would keep displaying the old value."
            ),
        )
        parser.add_argument(
            "--force-delegate-override", action="store_true",
            help=(
                "Write delegate_paid_or_free on every matched row, even where "
                "the row already displays the workbook's value by inheriting it "
                "from the invoice. Off by default, because the default compares "
                "against the value the Bookings table actually shows, which is "
                "the delegate override when set and the invoice's value "
                "otherwise, and so writes only the rows that are wrong."
            ),
        )
        parser.add_argument(
            "--allow-clear", action="store_true",
            help="Let a blank cell erase a stored value; off by default.",
        )
        parser.add_argument(
            "--fallback-email", action="store_true",
            help=(
                "For a row that Invoice Number plus Name could not match, try "
                "Invoice Number plus Delegate Email as a second attempt."
            ),
        )
        parser.add_argument(
            "--touch-updated-at", action="store_true",
            help="Bump updated_at on every changed row; off by default.",
        )
        parser.add_argument(
            "--report", default=None,
            help="Write a markdown report of every change and every problem here.",
        )
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Read only the first N data rows, for a quick look.",
        )

    # -- workbook -------------------------------------------------------------
    def _resolve_path(self, given: Optional[str]) -> Path:
        if given:
            path = Path(given)
            candidates = [path] if path.is_absolute() else [
                Path.cwd() / path, Path(settings.BASE_DIR) / path,
            ]
        else:
            candidates = [Path(settings.BASE_DIR) / DEFAULT_WORKBOOK]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise CommandError(
            "Workbook not found; looked at "
            + ", ".join(str(c) for c in candidates)
        )

    def _read_rows(self, path: Path, sheet: Optional[str], limit: Optional[int]):
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        try:
            ws = wb[sheet] if sheet else wb.worksheets[0]
            rows = ws.iter_rows(values_only=True)
            try:
                header = next(rows)
            except StopIteration:
                raise CommandError("The worksheet is empty.")

            index: dict[str, int] = {}
            for pos, cell in enumerate(header):
                key = _header_key(cell)
                for field, aliases in COLUMN_ALIASES.items():
                    if key in aliases and field not in index:
                        index[field] = pos

            missing = [f for f in ("invoice", "name") if f not in index]
            if missing:
                raise CommandError(
                    "Required column(s) not found, "
                    + ", ".join(missing)
                    + "; headers present are "
                    + ", ".join(_cell(c) for c in header if _cell(c))
                )

            def get(row, field):
                pos = index.get(field)
                if pos is None or pos >= len(row):
                    return ""
                return _cell(row[pos])

            records = []
            empty_rows = 0
            for offset, row in enumerate(rows):
                if limit is not None and len(records) >= limit:
                    break
                # openpyxl walks the sheet's whole used range, which on an
                # exported workbook runs past the data into trailing blank rows.
                # Those are not import problems and are not counted as any.
                if not any(_cell(c) for c in row):
                    empty_rows += 1
                    continue
                records.append({
                    "row_no": offset + 2,   # the header is row 1
                    "invoice": get(row, "invoice"),
                    "name": get(row, "name"),
                    "del_num": get(row, "delegate_number"),
                    "paid_free": get(row, "paid_or_free"),
                    "email": get(row, "email"),
                })
            return records, index, empty_rows, [_cell(h) for h in header]
        finally:
            wb.close()

    # -- main -----------------------------------------------------------------
    def handle(self, *args, **opts):
        path = self._resolve_path(opts["excel_path"])
        apply_changes = opts["apply"]
        prefix = "" if apply_changes else "[DRY RUN] "
        want_num = opts["fields"] in ("both", "delegate-number")
        want_pf = opts["fields"] in ("both", "paid-free")
        pf_target = opts["paid_free_target"]
        allow_clear = opts["allow_clear"]
        force_override = opts["force_delegate_override"]

        self.stdout.write(f"Reading {path} ...")
        records, index, empty_rows, headers = self._read_rows(
            path, opts["sheet"], opts["limit"]
        )
        self.stdout.write(
            f"  {len(records):,} data rows read"
            + (f", {empty_rows:,} blank row(s) ignored." if empty_rows else ".")
        )
        # Which header each field was taken from. A workbook spelling a column
        # differently is exactly how this data went wrong in the first place, so
        # the mapping this run used is never left implicit.
        for field, label in (
            ("invoice", "Invoice number"), ("name", "Name"),
            ("delegate_number", "Delegate Number"),
            ("paid_or_free", "Payable / Free"), ("email", "Email"),
        ):
            if field in index:
                self.stdout.write(
                    f"  {label:16} <- column {index[field] + 1}, "
                    f"{headers[index[field]]!r}"
                )

        if want_num and "delegate_number" not in index:
            raise CommandError(
                "No Delegate Number column in this workbook; pass "
                "--fields paid-free to update the other column alone."
            )
        if want_pf and "paid_or_free" not in index:
            raise CommandError(
                "No Paid/Free column in this workbook; pass "
                "--fields delegate-number to update the other column alone."
            )
        if opts["fallback_email"] and "email" not in index:
            self.stdout.write(self.style.WARNING(
                "  --fallback-email given, but this workbook has no email column."
            ))

        # -- stored rows, indexed the two ways the workbook is matched --------
        stored = defaultdict(list)
        stored_by_email = defaultdict(list)
        # EVERY delegate on an invoice, workbook row or not. The Payable / Free
        # decision is per invoice, and it cannot be made safely from the covered
        # rows alone; moving the invoice's value also moves what every
        # uncovered delegate on it displays.
        by_invoice = defaultdict(list)
        by_pk = {}
        invoice_paid_or_free = {}
        fields = (
            "id", "invoice_id", "first_name", "last_name", "email",
            "delegate_number", "delegate_paid_or_free", "invoice__paid_or_free",
        )
        for (pk, inv, first, last, email, del_num, del_pf, inv_pf) in (
            BookDelegate.objects.values_list(*fields).order_by("id")
        ):
            ikey = _invoice_key(inv)
            nkey = _name_key(f"{first} {last}")
            row = {
                "pk": pk,
                "invoice": inv,
                "ikey": ikey,
                "name": " ".join(f"{first} {last}".split()),
                "delegate_number": del_num,
                "delegate_paid_or_free": del_pf,
                "invoice_paid_or_free": inv_pf,
            }
            stored[(ikey, nkey)].append(row)
            by_invoice[ikey].append(row)
            if email:
                stored_by_email[(ikey, email.strip().lower())].append(row)
            by_pk[pk] = row
            invoice_paid_or_free[ikey] = inv_pf
        self.stdout.write(f"  {len(by_pk):,} stored delegates indexed.")

        # Invoice numbers on their own, so an unmatched row can say whether the
        # booking is missing altogether or only this person on it is missing.
        stored_invoices = {
            _invoice_key(n)
            for n in BookEvent.objects.values_list("invoice_number", flat=True)
        }

        # -- pair workbook rows onto stored rows -----------------------------
        grouped = defaultdict(list)
        no_invoice = []
        no_name = []
        for rec in records:
            if not rec["invoice"]:
                no_invoice.append(rec)
                continue
            if not rec["name"]:
                no_name.append(rec)
                continue
            grouped[(_invoice_key(rec["invoice"]), _name_key(rec["name"]))].append(rec)

        pairs: list[tuple[dict, dict]] = []
        unmatched: list[dict] = []
        ambiguous: list[tuple[dict, int, int]] = []
        used_pks: set[int] = set()

        for key, recs in grouped.items():
            candidates = [c for c in stored.get(key, []) if c["pk"] not in used_pks]
            if not candidates:
                unmatched.extend(recs)
                continue
            if len(recs) == len(candidates):
                # The same name twice on one invoice in both places, so order
                # pairs them; stored rows come back ordered by id, workbook rows
                # keep their sheet position.
                for rec, cand in zip(recs, candidates):
                    pairs.append((rec, cand))
                    used_pks.add(cand["pk"])
                continue
            # Counts disagree, so which stored row a workbook row means is not
            # decidable from the invoice and the name alone. Left untouched.
            for rec in recs:
                ambiguous.append((rec, len(recs), len(candidates)))

        # A second attempt on the email, for rows the name could not place.
        email_rescued = 0
        if opts["fallback_email"] and "email" in index:
            still_unmatched = []
            for rec in unmatched:
                key = (_invoice_key(rec["invoice"]), rec["email"].strip().lower())
                candidates = [
                    c for c in stored_by_email.get(key, [])
                    if c["pk"] not in used_pks
                ]
                if rec["email"] and len(candidates) == 1:
                    pairs.append((rec, candidates[0]))
                    used_pks.add(candidates[0]["pk"])
                    email_rescued += 1
                else:
                    still_unmatched.append(rec)
            unmatched = still_unmatched

        # Why each survivor could not be placed. A row whose email does match is
        # a name spelling difference; a row whose invoice is absent is a booking
        # that never landed; anything else is one missing person on an invoice
        # that did land.
        for rec in unmatched:
            key = (_invoice_key(rec["invoice"]), rec["email"].strip().lower())
            rec["email_would_match"] = bool(rec["email"]) and bool(
                stored_by_email.get(key)
            )
            rec["invoice_exists"] = _invoice_key(rec["invoice"]) in stored_invoices
            if rec["email_would_match"]:
                rec["note"] = "the email matches, the name differs"
            elif not rec["invoice_exists"]:
                rec["note"] = "the invoice is not in the CRM at all"
            else:
                rec["note"] = "the invoice is in the CRM, this delegate is not"

        # -- work out the writes ---------------------------------------------
        delegate_writes: dict[int, dict] = {}
        invoice_writes: dict[str, str] = {}
        changes: list[tuple] = []
        bad_numbers: list[tuple] = []
        bad_paid_free: list[tuple] = []
        out_of_range: list[tuple] = []
        stale_overrides: list[tuple] = []
        partial_invoices: list[tuple] = []
        correct_num = 0
        correct_pf = 0
        pf_display_change = 0
        blank_skipped = 0

        def queue(pk, field, old, new, invoice, name):
            delegate_writes.setdefault(pk, {})[field] = new
            changes.append((invoice, name, field, old, new))

        for rec, cand in pairs:
            row_changes = {}

            if want_num:
                value, ok = _delegate_number(rec["del_num"])
                if not ok:
                    bad_numbers.append((rec, rec["del_num"]))
                elif value is None:
                    # An integer column has no blank to be cleared to, so
                    # --allow-clear does not apply here.
                    blank_skipped += 1
                else:
                    if value not in (0, 1):
                        out_of_range.append((rec, value))
                    if value != cand["delegate_number"]:
                        row_changes["delegate_number"] = value
                        changes.append((
                            rec["invoice"], cand["name"], "delegate_number",
                            cand["delegate_number"], value,
                        ))
                    else:
                        correct_num += 1

            if row_changes:
                delegate_writes.setdefault(cand["pk"], {}).update(row_changes)

        # -- Payable / Free, worked out one INVOICE at a time -----------------
        #
        # WHY THIS IS NOT A PER-ROW DECISION
        # The displayed value is `delegate_paid_or_free or invoice.paid_or_free`,
        # see the three serializers in book_delegate/serializers.py, so the same
        # fact lives in two columns and only their combination is the answer.
        # Writing an override per row does make the Bookings table read right,
        # and it leaves invoice.paid_or_free holding the old value for everything
        # that reads the invoice directly; the read-only `paid_or_free` field on
        # the delegate serializers, BookEventFilter, the sync export, and the
        # parent half of the bulk-update spec, which labels the invoice column
        # "Payable / Free" and the override column "Payable / Free (override)".
        # Two columns, one label, two answers is what makes this look like it
        # did not update.
        #
        # frontend/src/api/bookings.js states the rule the CRM itself follows,
        # and this is that rule. Where every delegate on an invoice agrees, the
        # value goes on the INVOICE and the overrides are cleared; an override
        # survives only to carry a genuine per-delegate difference.
        pf_pending: dict[int, str] = {}
        if want_pf:
            for rec, cand in pairs:
                value, ok = _paid_or_free(rec["paid_free"])
                if not ok:
                    bad_paid_free.append((rec, rec["paid_free"]))
                elif value is None:
                    if allow_clear and cand["delegate_paid_or_free"] is not None:
                        queue(cand["pk"], "delegate_paid_or_free",
                              cand["delegate_paid_or_free"], None,
                              rec["invoice"], cand["name"])
                    else:
                        blank_skipped += 1
                else:
                    pf_pending[cand["pk"]] = value

        for ikey, members in by_invoice.items():
            covered = [m for m in members if m["pk"] in pf_pending]
            if not covered:
                continue
            raw_invoice = members[0]["invoice"]
            stored_invoice_pf = members[0]["invoice_paid_or_free"] or ""
            full_coverage = len(covered) == len(members)

            # What each covered delegate DISPLAYS today, counted before any
            # target-specific logic. This is the number a reader of the summary
            # means by "already correct"; the per-column write counts below
            # cannot answer it, because a delegate whose override is correctly
            # NULL still displays the wrong value when the invoice is wrong.
            for m in covered:
                shown = m["delegate_paid_or_free"] or stored_invoice_pf
                if pf_pending[m["pk"]] == shown:
                    correct_pf += 1
                else:
                    pf_display_change += 1

            if pf_target == "sync" and not full_coverage:
                # The workbook does not account for everybody on this booking, so
                # what the invoice should say is not knowable from it. Moving the
                # invoice would re-label the delegates it never mentioned, so the
                # invoice is left alone and the rows that are covered are carried
                # as per-delegate differences instead.
                partial_invoices.append(
                    (raw_invoice, len(covered), len(members))
                )
                for m in covered:
                    value = pf_pending[m["pk"]]
                    shown = m["delegate_paid_or_free"] or stored_invoice_pf
                    if value != shown:
                        queue(m["pk"], "delegate_paid_or_free", shown, value,
                              raw_invoice, m["name"])
                continue

            if pf_target == "delegate":
                # Overrides only, the invoice untouched. Kept for the case where
                # somebody deliberately does not want invoice rows written.
                for m in covered:
                    value = pf_pending[m["pk"]]
                    shown = (
                        m["delegate_paid_or_free"] if force_override
                        else (m["delegate_paid_or_free"] or stored_invoice_pf)
                    )
                    if value != shown:
                        queue(m["pk"], "delegate_paid_or_free", shown, value,
                              raw_invoice, m["name"])
                continue

            # The value the invoice should carry. Unanimous among the covered
            # rows where they agree; otherwise the most common, with the value
            # already stored breaking a tie so a coin flip does not rewrite it.
            tally = Counter(pf_pending[m["pk"]] for m in covered)
            ranked = tally.most_common()
            invoice_value = ranked[0][0]
            if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
                tied = {v for v, n in ranked if n == ranked[0][1]}
                if stored_invoice_pf in tied:
                    invoice_value = stored_invoice_pf

            if invoice_value != stored_invoice_pf:
                invoice_writes[raw_invoice] = invoice_value
                changes.append((raw_invoice, "(invoice)", "invoice.paid_or_free",
                                stored_invoice_pf or None, invoice_value))

            if pf_target == "invoice":
                # The invoice alone, by explicit request. A stale override on a
                # covered row would keep displaying the old value, so those rows
                # are reported rather than silently left wrong.
                for m in covered:
                    stale = (
                        m["delegate_paid_or_free"]
                        and m["delegate_paid_or_free"] != pf_pending[m["pk"]]
                    )
                    if stale:
                        stale_overrides.append((raw_invoice, m["name"],
                                                m["delegate_paid_or_free"],
                                                pf_pending[m["pk"]]))
                continue

            # Full coverage, so every delegate on this booking is accounted for.
            # An override is needed only where a person differs from what the
            # invoice now says; where they agree, clearing it restores
            # inheritance, which is the state the booking modal leaves behind and
            # the state a report reading the invoice can be trusted in.
            for m in covered:
                stored_override = m["delegate_paid_or_free"] or None
                value = pf_pending[m["pk"]]
                wanted = None if value == invoice_value else value
                if wanted != stored_override:
                    queue(m["pk"], "delegate_paid_or_free",
                          stored_override, wanted, raw_invoice, m["name"])

        # -- report to the console -------------------------------------------
        w = self.stdout.write
        w("")
        w(f"{prefix}Matched on invoice plus name : {len(pairs):,}")
        if email_rescued:
            w(f"{prefix}Matched on invoice plus email: {email_rescued:,}")
        w(f"{prefix}Unmatched workbook rows      : {len(unmatched):,}")
        w(f"{prefix}Ambiguous workbook rows      : {len(ambiguous):,}")
        if no_invoice:
            w(f"{prefix}Rows with no invoice number  : {len(no_invoice):,}")
        if no_name:
            w(f"{prefix}Rows with no name            : {len(no_name):,}")
        w("")
        num_changes = sum(1 for c in changes if c[2] == "delegate_number")
        pf_changes = sum(1 for c in changes if c[2] == "delegate_paid_or_free")
        inv_changes = sum(1 for c in changes if c[2] == "invoice.paid_or_free")
        w(f"{prefix}Payable / Free target           : {pf_target}")
        w(f"{prefix}delegate_number to change       : {num_changes:,}")
        w(f"{prefix}invoice paid_or_free to change  : {inv_changes:,}")
        w(f"{prefix}delegate override to change     : {pf_changes:,}")
        if partial_invoices:
            w(f"{prefix}Invoices left alone because the ")
            w(f"{prefix}workbook covers only some of    ")
            w(f"{prefix}their delegates                 : {len(partial_invoices):,}")
        w(f"{prefix}Delegates whose displayed       ")
        w(f"{prefix}Payable / Free is wrong today   : {pf_display_change:,}")
        w(f"{prefix}delegate_number already correct : {correct_num:,}")
        w(f"{prefix}Payable / Free already correct  : {correct_pf:,}")
        w(f"{prefix}Blank cells skipped             : {blank_skipped:,}")
        w(f"{prefix}Delegate rows to write          : {len(delegate_writes):,}")

        if stale_overrides:
            w(self.style.WARNING(
                f"\n--paid-free-target invoice leaves {len(stale_overrides)} "
                "row(s) displaying the OLD value, because a per-delegate "
                "override outranks the invoice."
            ))
            for inv, name, override, wanted in stale_overrides[:10]:
                w(self.style.WARNING(
                    f"  {inv} / {name}, override {override!r} outranks the "
                    f"workbook's {wanted!r}"
                ))
            if len(stale_overrides) > 10:
                w(self.style.WARNING(
                    f"  and {len(stale_overrides) - 10} more. Re-run with the "
                    "default --paid-free-target sync to settle them."
                ))

        for label, items in (
            ("Unreadable Delegate Number cell", bad_numbers),
            ("Unreadable Paid/Free cell", bad_paid_free),
            ("Delegate Number outside 0 and 1", out_of_range),
        ):
            if items:
                w(self.style.WARNING(f"\n{label}, {len(items)} row(s)."))
                for rec, value in items[:10]:
                    w(self.style.WARNING(
                        f"  row {rec['row_no']}, {rec['invoice']} / "
                        f"{rec['name']}, value {value!r}"
                    ))
                if len(items) > 10:
                    w(self.style.WARNING(f"  and {len(items) - 10} more."))

        if unmatched:
            near = sum(1 for r in unmatched if r.get("email_would_match"))
            w(self.style.WARNING(f"\nUnmatched, {len(unmatched)} row(s)."))
            for rec in unmatched[:15]:
                w(self.style.WARNING(
                    f"  row {rec['row_no']}, {rec['invoice']} / "
                    f"{rec['name']}; {rec['note']}"
                ))
            if len(unmatched) > 15:
                w(self.style.WARNING(f"  and {len(unmatched) - 15} more."))
            if near:
                w(self.style.WARNING(
                    f"  {near} of them match on the email; re-run with "
                    "--fallback-email to include them."
                ))

        if ambiguous:
            w(self.style.WARNING(
                f"\nAmbiguous, {len(ambiguous)} row(s), left untouched."
            ))
            for rec, n_wb, n_db in ambiguous[:10]:
                w(self.style.WARNING(
                    f"  row {rec['row_no']}, {rec['invoice']} / {rec['name']}, "
                    f"{n_wb} workbook row(s) against {n_db} stored row(s)"
                ))
            if len(ambiguous) > 10:
                w(self.style.WARNING(f"  and {len(ambiguous) - 10} more."))

        # -- markdown report -------------------------------------------------
        if opts["report"]:
            self._write_report(
                Path(opts["report"]), path, pf_target, changes, unmatched,
                ambiguous, bad_numbers, bad_paid_free, out_of_range,
                stale_overrides, partial_invoices, len(pairs), len(records),
            )
            w(f"\n{prefix}Report written to {opts['report']}")

        if not apply_changes:
            w(self.style.SUCCESS(
                "\nDry run complete, nothing written. Pass --apply to write."
            ))
            return

        if not delegate_writes and not invoice_writes:
            w(self.style.SUCCESS(
                "\nNothing to write; the stored data already agrees."
            ))
            return

        # -- write -----------------------------------------------------------
        write_fields = sorted({f for ch in delegate_writes.values() for f in ch})
        if opts["touch_updated_at"]:
            write_fields.append("updated_at")
        now = timezone.now()

        objs = []
        for pk, row_changes in delegate_writes.items():
            current = by_pk[pk]
            obj = BookDelegate(id=pk)
            for field in write_fields:
                if field == "updated_at":
                    obj.updated_at = now
                else:
                    setattr(obj, field, row_changes.get(field, current[field]))
            objs.append(obj)

        with transaction.atomic():
            # bulk_update, so BookDelegate.save() does not run and no third
            # column is rewritten as a side effect of this correction. Guarded,
            # because a run whose only change is at the invoice level leaves
            # write_fields empty and bulk_update rejects an empty field list.
            if objs and write_fields:
                BookDelegate.objects.bulk_update(
                    objs, write_fields, batch_size=500
                )
            by_value = defaultdict(list)
            for inv, value in invoice_writes.items():
                by_value[value].append(inv)
            for value, invoices in by_value.items():
                for start in range(0, len(invoices), 500):
                    BookEvent.objects.filter(
                        invoice_number__in=invoices[start:start + 500]
                    ).update(paid_or_free=value)

        parts = []
        if objs and write_fields:
            parts.append(
                f"{len(objs):,} delegate row(s) on {', '.join(write_fields)}"
            )
        if invoice_writes:
            parts.append(f"{len(invoice_writes):,} invoice row(s) on paid_or_free")
        w(self.style.SUCCESS("\nDone. Updated " + "; ".join(parts) + "."))

    # -- report file ----------------------------------------------------------
    def _write_report(self, out, workbook, pf_target, changes, unmatched,
                      ambiguous, bad_numbers, bad_paid_free, out_of_range,
                      stale_overrides, partial_invoices, matched, total):
        def esc(v):
            return str("" if v is None else v).replace("|", "\\|")

        lines = [
            "# Delegate Number and Payable / Free update\n\n",
            f"Source workbook, `{workbook}`.\n\n",
            f"{total:,} workbook rows read; {matched:,} matched on Invoice "
            f"Number plus Name.\n\n",
            f"{len(changes):,} value(s) differ from what is stored.\n\n",
            "## Changes\n\n",
            "| Invoice | Delegate | Field | Stored | Workbook |\n",
            "|---|---|---|---|---|\n",
        ]
        for inv, name, field, old, new in changes[:5000]:
            lines.append(
                f"| `{esc(inv)}` | {esc(name)} | {field} | "
                f"`{esc(old)}` | `{esc(new)}` |\n"
            )
        if len(changes) > 5000:
            lines.append(f"\n_and {len(changes) - 5000} more change(s)._\n")

        if unmatched:
            lines.append("\n## Unmatched workbook rows\n\n")
            lines.append("| Row | Invoice | Name | Note |\n|---|---|---|---|\n")
            for rec in unmatched:
                lines.append(
                    f"| {rec['row_no']} | `{esc(rec['invoice'])}` | "
                    f"{esc(rec['name'])} | {esc(rec.get('note', ''))} |\n"
                )

        if ambiguous:
            lines.append("\n## Ambiguous workbook rows, left untouched\n\n")
            lines.append("| Row | Invoice | Name | Workbook rows | Stored rows |\n")
            lines.append("|---|---|---|---|---|\n")
            for rec, n_wb, n_db in ambiguous:
                lines.append(
                    f"| {rec['row_no']} | `{esc(rec['invoice'])}` | "
                    f"{esc(rec['name'])} | {n_wb} | {n_db} |\n"
                )

        for title, items in (
            ("Unreadable Delegate Number cells", bad_numbers),
            ("Unreadable Paid/Free cells", bad_paid_free),
            ("Delegate Number values outside 0 and 1", out_of_range),
        ):
            if items:
                lines.append(f"\n## {title}\n\n")
                lines.append("| Row | Invoice | Name | Value |\n|---|---|---|---|\n")
                for rec, value in items:
                    lines.append(
                        f"| {rec['row_no']} | `{esc(rec['invoice'])}` | "
                        f"{esc(rec['name'])} | `{esc(value)}` |\n"
                    )

        out.write_text("".join(lines), encoding="utf-8")
