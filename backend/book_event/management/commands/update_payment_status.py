"""
management command, update_payment_status

Updates ONE displayed value from an Excel workbook, and nothing else.

    Payment Status  ->  the Payment Status the CRM displays, which lives in
                        BookEvent.payment_status and in the per-delegate
                        override BookDelegate.delegate_payment_status together.

A workbook row is matched onto a stored delegate on the pair Invoice Number
plus Name; nothing else is used to match, and no other column is written. The
workbook's Payment Date, Payment Type, Ticket Tier and Delegate Count columns
are handled by their own commands and are deliberately ignored here.

PAYMENT STATUS IS TWO COLUMNS, NOT ONE
Every serializer resolves it as `delegate_payment_status or
invoice.payment_status`, three times in book_delegate/serializers.py, and
accounts/filter_spec.py filters it the same way, so the same fact lives in two
columns and only their combination is the answer. Writing one and not the other
produces a booking that answers the question differently depending on who asks.
That is the failure this command is built to avoid, and it is why --target
defaults to sync.

  sync      the invoice carries the value the delegates agree on, and an
            override survives only where one delegate genuinely differs. This is
            the rule frontend/src/api/bookings.js states and the booking modal
            follows, so a workbook update and a hand edit leave the same shape.
  delegate  the override only. The Bookings table reads right and
            invoice.payment_status keeps the old value, which BookEventFilter,
            the read-only `payment_status` serializer field, the sync export and
            the parent half of the bulk-update spec all still read.
  invoice   the invoice only. A delegate carrying an override keeps displaying
            the old value, so those rows are reported instead of being left
            quietly wrong.

Under sync, an invoice is moved only when the workbook accounts for EVERY
delegate on it. Where it covers only some of them, what the invoice should say
is not knowable from the file, and moving it would re-label the people the file
never mentioned, so the invoice is left alone and the covered rows are carried
as per-delegate differences instead. Those invoices are counted in the summary,
so a workbook that is a partial extract does not look like a silent no-op.

DELEGATE COUNT FOLLOWS A CANCELLED OVERRIDE
BookDelegate.save() forces delegate_count to 0 while delegate_payment_status is
"Cancelled", and restores it to 1 on the transition off Cancelled. Writes here
go through bulk_update, which does not run save(), so that one rule is applied
by hand for the rows whose OVERRIDE moves into or out of Cancelled. save()
reads the override and not the invoice, so an invoice-level Cancelled does not
move delegate_count, here or anywhere else.

SAFETY
  - Dry run is the DEFAULT. Pass --apply to write.
  - Writes go through bulk_update, so BookDelegate.save() never runs and cannot
    touch event_code, booking_code or booked_on as a side effect.
  - A blank cell is skipped, never written, unless --allow-clear is passed.
  - Only values in BookEvent.PaymentStatus are accepted; anything else is
    reported and its row left alone.
  - updated_at is left alone unless --touch-updated-at is passed. payment_status
    is in BookEvent.DELEGATE_EXPORT_FIELDS, so an incremental consumer of the
    Data API delta feed, ?updated_since=, will NOT pick these corrections up
    without it; the run warns when that applies.

RELATED
`update_delegate_number_paid_free` does the same job for Delegate Number and
Payable / Free, and this command reuses its cell, header, invoice and name
readers so both place a workbook row on the same stored delegate.

Usage
    python manage.py update_payment_status "path/to/file.xlsx"
    python manage.py update_payment_status "file.xlsx" --sheet Sheet2 --report r.md
    python manage.py update_payment_status "file.xlsx" --apply --touch-updated-at
"""
from __future__ import annotations

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

# One reader for a cell, a header, an invoice number and a name, shared with the
# sibling command so a row that matches there matches here.
from .update_delegate_number_paid_free import (
    _cell,
    _header_key,
    _invoice_key,
    _name_key,
)

CANCELLED = "Cancelled"

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "invoice": ("invoice number", "invoice no", "invoice no.", "invoice #",
                "invoice_number", "invoice"),
    "name": ("name", "delegate name", "full name", "delegate"),
    "status": ("payment status", "payment_status", "status",
               "payment status (invoice)"),
    "email": ("delegate email", "email", "email address", "delegate_email"),
}

# Stored spelling for every value this column may hold, keyed by a lowered,
# single-spaced cell. The model's own choices and nothing invented on top; a
# value this map does not know is reported rather than guessed at, because the
# column is choice-validated everywhere else and a coined value would fail
# full_clean() and vanish from every choice-driven filter.
STATUS_LOOKUP = {
    " ".join(v.lower().split()): v for v in BookEvent.PaymentStatus.values
}


def _status(raw: str) -> tuple[Optional[str], bool]:
    """
    A Payment Status cell as the stored spelling, plus whether it was readable.

    Returns (None, True) for a blank cell, and (None, False) for a cell holding
    something this column cannot mean.
    """
    if not raw:
        return None, True
    key = " ".join(raw.strip().lower().split())
    if key in ("-", "n/a", "na"):
        return None, True
    value = STATUS_LOOKUP.get(key)
    return value, value is not None


class Command(BaseCommand):
    help = (
        "Update the Payment Status column from an Excel workbook, matching rows "
        "on Invoice Number plus Name. No other field is written. Dry run unless "
        "--apply is passed."
    )

    def add_arguments(self, parser):
        parser.add_argument("excel_path", help="Path to the workbook.")
        parser.add_argument(
            "--sheet", default=None,
            help="Worksheet name; defaults to the first sheet.",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the changes. Without it the command only reports.",
        )
        parser.add_argument(
            "--target", choices=("sync", "delegate", "invoice"), default="sync",
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
            "--allow-clear", action="store_true",
            help="Let a blank cell clear a per-delegate override; off by default.",
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
            help=(
                "Bump updated_at on every delegate whose displayed status moves, "
                "so the Data API delta feed re-exports them. Off by default, "
                "because it reshuffles a table sorted on last modified."
            ),
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
    def _resolve_path(self, given: str) -> Path:
        path = Path(given)
        candidates = [path] if path.is_absolute() else [
            Path.cwd() / path, Path(settings.BASE_DIR) / path,
        ]
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

            # A sheet whose first row is DATA rather than headers lands here, and
            # that is a real shape rather than a hypothetical one: a workbook can
            # carry the same rows twice, once with a header row and once without.
            # Reading the headerless copy would consume one booking as the header
            # and mis-place every column, so it stops instead.
            missing = [f for f in ("invoice", "name", "status") if f not in index]
            if missing:
                raise CommandError(
                    "Required column(s) not found, "
                    + ", ".join(missing)
                    + "; headers present are "
                    + ", ".join(_cell(c) for c in header if _cell(c))
                    + ". Pass --sheet to name the worksheet that has a header row."
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
                    "status": get(row, "status"),
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
        target = opts["target"]
        allow_clear = opts["allow_clear"]

        self.stdout.write(f"Reading {path} ...")
        records, index, empty_rows, headers = self._read_rows(
            path, opts["sheet"], opts["limit"]
        )
        self.stdout.write(
            f"  {len(records):,} data rows read"
            + (f", {empty_rows:,} blank row(s) ignored." if empty_rows else ".")
        )
        # Which header each field was taken from. A workbook spelling a column
        # differently is exactly how this data goes wrong, so the mapping this
        # run used is never left implicit.
        for field, label in (
            ("invoice", "Invoice number"), ("name", "Name"),
            ("status", "Payment Status"), ("email", "Email"),
        ):
            if field in index:
                self.stdout.write(
                    f"  {label:16} <- column {index[field] + 1}, "
                    f"{headers[index[field]]!r}"
                )
        if opts["fallback_email"] and "email" not in index:
            self.stdout.write(self.style.WARNING(
                "  --fallback-email given, but this workbook has no email column."
            ))

        # -- stored rows, indexed the two ways the workbook is matched --------
        stored = defaultdict(list)
        stored_by_email = defaultdict(list)
        # EVERY delegate on an invoice, workbook row or not. The status decision
        # is per invoice and cannot be made safely from the covered rows alone;
        # moving the invoice's value also moves what every uncovered delegate on
        # it displays.
        by_invoice = defaultdict(list)
        by_pk = {}
        fields = (
            "id", "invoice_id", "first_name", "last_name", "email",
            "delegate_payment_status", "delegate_count",
            "invoice__payment_status",
        )
        for (pk, inv, first, last, email, del_st, del_ct, inv_st) in (
            BookDelegate.objects.values_list(*fields).order_by("id")
        ):
            ikey = _invoice_key(inv)
            nkey = _name_key(f"{first} {last}")
            row = {
                "pk": pk,
                "invoice": inv,
                "ikey": ikey,
                "name": " ".join(f"{first} {last}".split()),
                "delegate_payment_status": del_st,
                "delegate_count": del_ct,
                "invoice_payment_status": inv_st,
            }
            stored[(ikey, nkey)].append(row)
            by_invoice[ikey].append(row)
            if email:
                stored_by_email[(ikey, email.strip().lower())].append(row)
            by_pk[pk] = row
        self.stdout.write(f"  {len(by_pk):,} stored delegates indexed.")

        # Invoice numbers on their own, so an unmatched row can say whether the
        # booking is missing altogether or only this person on it is missing.
        stored_invoices = {
            _invoice_key(n)
            for n in BookEvent.objects.values_list("invoice_number", flat=True)
        }

        # -- pair workbook rows onto stored rows -----------------------------
        grouped = defaultdict(list)
        no_invoice, no_name = [], []
        for rec in records:
            if not rec["invoice"]:
                no_invoice.append(rec)
                continue
            if not rec["name"]:
                no_name.append(rec)
                continue
            grouped[
                (_invoice_key(rec["invoice"]), _name_key(rec["name"]))
            ].append(rec)

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
        bad_status: list[tuple] = []
        stale_overrides: list[tuple] = []
        partial_invoices: list[tuple] = []
        touched_invoices: set[str] = set()
        already_correct = 0
        display_change = 0
        blank_skipped = 0

        def queue(pk, field, old, new, invoice, name):
            delegate_writes.setdefault(pk, {})[field] = new
            changes.append((invoice, name, field, old, new))

        def set_override(m, wanted, invoice):
            """
            Write the override, and with it the one column save() derives from it.

            BookDelegate.save() forces delegate_count to 0 while the override is
            Cancelled and restores 1 on the transition off it. bulk_update does
            not run save(), so that rule is applied here rather than left to
            drift. Every other column save() derives is untouched by this
            command's inputs and is deliberately not rewritten.
            """
            stored_override = m["delegate_payment_status"] or None
            if wanted == stored_override:
                return
            queue(m["pk"], "delegate_payment_status", stored_override, wanted,
                  invoice, m["name"])
            if wanted == CANCELLED:
                if m["delegate_count"] != 0:
                    queue(m["pk"], "delegate_count", m["delegate_count"], 0,
                          invoice, m["name"])
            elif stored_override == CANCELLED and m["delegate_count"] != 1:
                queue(m["pk"], "delegate_count", m["delegate_count"], 1,
                      invoice, m["name"])

        # A readable status per matched row, before any per-invoice decision.
        pending: dict[int, str] = {}
        for rec, cand in pairs:
            value, ok = _status(rec["status"])
            if not ok:
                bad_status.append((rec, rec["status"]))
            elif value is None:
                if allow_clear and cand["delegate_payment_status"] is not None:
                    set_override(cand, None, rec["invoice"])
                else:
                    blank_skipped += 1
            else:
                pending[cand["pk"]] = value

        # -- the status, worked out one INVOICE at a time ----------------------
        for ikey, members in by_invoice.items():
            covered = [m for m in members if m["pk"] in pending]
            if not covered:
                continue
            raw_invoice = members[0]["invoice"]
            stored_invoice_st = members[0]["invoice_payment_status"] or ""
            full_coverage = len(covered) == len(members)

            # What each covered delegate DISPLAYS today, counted before any
            # target-specific logic. This is the number a reader of the summary
            # means by "already correct"; the per-column write counts below
            # cannot answer it, because a delegate whose override is correctly
            # NULL still displays the wrong value when the invoice is wrong.
            for m in covered:
                shown = m["delegate_payment_status"] or stored_invoice_st
                if pending[m["pk"]] == shown:
                    already_correct += 1
                else:
                    display_change += 1
                    touched_invoices.add(raw_invoice)

            if target == "sync" and not full_coverage:
                # The workbook does not account for everybody on this booking, so
                # what the invoice should say is not knowable from it. Moving the
                # invoice would re-label the delegates it never mentioned, so the
                # invoice is left alone and the rows that are covered are carried
                # as per-delegate differences instead.
                partial_invoices.append((raw_invoice, len(covered), len(members)))
                for m in covered:
                    value = pending[m["pk"]]
                    shown = m["delegate_payment_status"] or stored_invoice_st
                    if value != shown:
                        set_override(m, value, raw_invoice)
                continue

            if target == "delegate":
                # Overrides only, the invoice untouched. Kept for the case where
                # somebody deliberately does not want invoice rows written.
                for m in covered:
                    value = pending[m["pk"]]
                    shown = m["delegate_payment_status"] or stored_invoice_st
                    if value != shown:
                        set_override(m, value, raw_invoice)
                continue

            # The value the invoice should carry. Unanimous among the covered
            # rows where they agree; otherwise the most common, with the value
            # already stored breaking a tie so a coin flip does not rewrite it.
            tally = Counter(pending[m["pk"]] for m in covered)
            ranked = tally.most_common()
            invoice_value = ranked[0][0]
            if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
                tied = {v for v, n in ranked if n == ranked[0][1]}
                if stored_invoice_st in tied:
                    invoice_value = stored_invoice_st

            if invoice_value != stored_invoice_st:
                invoice_writes[raw_invoice] = invoice_value
                changes.append((
                    raw_invoice, "(invoice)", "invoice.payment_status",
                    stored_invoice_st or None, invoice_value,
                ))

            if target == "invoice":
                # The invoice alone, by explicit request. A stale override on a
                # covered row would keep displaying the old value, so those rows
                # are reported rather than silently left wrong.
                for m in covered:
                    if (m["delegate_payment_status"]
                            and m["delegate_payment_status"] != pending[m["pk"]]):
                        stale_overrides.append((
                            raw_invoice, m["name"],
                            m["delegate_payment_status"], pending[m["pk"]],
                        ))
                continue

            # Full coverage, so every delegate on this booking is accounted for.
            # An override is needed only where a person differs from what the
            # invoice now says; where they agree, clearing it restores
            # inheritance, which is the state the booking modal leaves behind and
            # the state a report reading the invoice can be trusted in.
            for m in covered:
                value = pending[m["pk"]]
                set_override(
                    m, None if value == invoice_value else value, raw_invoice
                )

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
        inv_changes = sum(1 for c in changes if c[2] == "invoice.payment_status")
        ov_changes = sum(1 for c in changes if c[2] == "delegate_payment_status")
        ct_changes = sum(1 for c in changes if c[2] == "delegate_count")
        w(f"{prefix}Payment Status target           : {target}")
        w(f"{prefix}invoice payment_status to change: {inv_changes:,}")
        w(f"{prefix}delegate override to change     : {ov_changes:,}")
        if ct_changes:
            w(f"{prefix}delegate_count following a      ")
            w(f"{prefix}Cancelled override              : {ct_changes:,}")
        if partial_invoices:
            w(f"{prefix}Invoices left alone because the ")
            w(f"{prefix}workbook covers only some of    ")
            w(f"{prefix}their delegates                 : {len(partial_invoices):,}")
        w(f"{prefix}Delegates whose displayed       ")
        w(f"{prefix}Payment Status is wrong today   : {display_change:,}")
        w(f"{prefix}Payment Status already correct  : {already_correct:,}")
        w(f"{prefix}Blank cells skipped             : {blank_skipped:,}")
        w(f"{prefix}Delegate rows to write          : {len(delegate_writes):,}")

        if invoice_writes and not opts["touch_updated_at"]:
            w(self.style.WARNING(
                "\npayment_status is in BookEvent.DELEGATE_EXPORT_FIELDS, and an "
                "invoice written here does not run BookEvent.save(), so the "
                "delegates' updated_at does not move. A consumer reading the "
                "Data API with ?updated_since= will keep showing the OLD status. "
                "Pass --touch-updated-at to stamp the affected rows."
            ))

        if stale_overrides:
            w(self.style.WARNING(
                f"\n--target invoice leaves {len(stale_overrides)} row(s) "
                "displaying the OLD value, because a per-delegate override "
                "outranks the invoice."
            ))
            for inv, name, override, wanted in stale_overrides[:10]:
                w(self.style.WARNING(
                    f"  {inv} / {name}, override {override!r} outranks the "
                    f"workbook's {wanted!r}"
                ))
            if len(stale_overrides) > 10:
                w(self.style.WARNING(
                    f"  and {len(stale_overrides) - 10} more. Re-run with the "
                    "default --target sync to settle them."
                ))

        if bad_status:
            w(self.style.WARNING(
                f"\nUnreadable Payment Status cell, {len(bad_status)} row(s)."
            ))
            for rec, value in bad_status[:10]:
                w(self.style.WARNING(
                    f"  row {rec['row_no']}, {rec['invoice']} / {rec['name']}, "
                    f"value {value!r}"
                ))
            if len(bad_status) > 10:
                w(self.style.WARNING(f"  and {len(bad_status) - 10} more."))
            w(self.style.WARNING(
                "  Accepted values are "
                + ", ".join(BookEvent.PaymentStatus.values) + "."
            ))

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
                Path(opts["report"]), path, target, changes, unmatched,
                ambiguous, bad_status, stale_overrides, partial_invoices,
                len(pairs), len(records),
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
        now = timezone.now()
        objs = []
        for pk, row_changes in delegate_writes.items():
            current = by_pk[pk]
            obj = BookDelegate(id=pk)
            for field in write_fields:
                setattr(obj, field, row_changes.get(field, current[field]))
            objs.append(obj)

        stamped = 0
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
                    ).update(payment_status=value)
            if opts["touch_updated_at"] and touched_invoices:
                # Every delegate whose DISPLAYED status moved, which is not the
                # same set as the rows written: an invoice-level correction moves
                # what its delegates display while writing none of their own
                # columns, and the delta feed is keyed on book_delegates.updated_at.
                invoices = sorted(touched_invoices)
                for start in range(0, len(invoices), 500):
                    stamped += BookDelegate.objects.filter(
                        invoice_id__in=invoices[start:start + 500]
                    ).update(updated_at=now)

        parts = []
        if objs and write_fields:
            parts.append(
                f"{len(objs):,} delegate row(s) on {', '.join(write_fields)}"
            )
        if invoice_writes:
            parts.append(
                f"{len(invoice_writes):,} invoice row(s) on payment_status"
            )
        if stamped:
            parts.append(f"{stamped:,} delegate row(s) stamped on updated_at")
        w(self.style.SUCCESS("\nDone. Updated " + "; ".join(parts) + "."))

    # -- report file ----------------------------------------------------------
    def _write_report(self, out, workbook, target, changes, unmatched, ambiguous,
                      bad_status, stale_overrides, partial_invoices, matched,
                      total):
        def esc(v):
            return str("" if v is None else v).replace("|", "\\|")

        lines = [
            "# Payment Status update\n\n",
            f"Source workbook, `{workbook}`. Target, `{target}`.\n\n",
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

        if partial_invoices:
            lines.append(
                "\n## Invoices left alone, the workbook covers only some of "
                "their delegates\n\n"
            )
            lines.append("| Invoice | Covered | Delegates |\n|---|---|---|\n")
            for inv, covered, members in partial_invoices:
                lines.append(f"| `{esc(inv)}` | {covered} | {members} |\n")

        if stale_overrides:
            lines.append("\n## Overrides that outrank the invoice\n\n")
            lines.append("| Invoice | Delegate | Override | Workbook |\n")
            lines.append("|---|---|---|---|\n")
            for inv, name, override, wanted in stale_overrides:
                lines.append(
                    f"| `{esc(inv)}` | {esc(name)} | `{esc(override)}` | "
                    f"`{esc(wanted)}` |\n"
                )

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

        if bad_status:
            lines.append("\n## Unreadable Payment Status cells\n\n")
            lines.append("| Row | Invoice | Name | Value |\n|---|---|---|---|\n")
            for rec, value in bad_status:
                lines.append(
                    f"| {rec['row_no']} | `{esc(rec['invoice'])}` | "
                    f"{esc(rec['name'])} | `{esc(value)}` |\n"
                )

        out.write_text("".join(lines), encoding="utf-8")
