"""
management command: import_booking_excel

Wipes ALL BookDelegate + BookEvent rows and re-imports from the given Excel
workbook. Every row in the workbook is one delegate; rows sharing the same
Invoice Number belong to the same invoice (BookEvent).

Excel column → model field mapping:
  Payment Status    → BookEvent.payment_status
  Event Code        → BookEvent.event_code  (resolved to canonical Event.event_code
                       via master_code + year lookup)
  Booking Code      → BookEvent.booking_code
  Invoice Date      → BookEvent.invoice_date
  Invoice Number    → BookEvent.invoice_number
  Delegate Company  → BookEvent.company_name  +  BookDelegate.company_name_raw
  Accounts Contact  → BookEvent.accounts_contact_email
  Paid/Free         → BookEvent.paid_or_free
  Date Paid         → BookEvent.payment_date
  Payment Type      → BookEvent.payment_type
  Ticket Tier       → BookEvent.ticket_tier
  Discount          → BookEvent.discount (a percentage or a fraction; falls
                       back to BookEvent.discount_code when the cell is not
                       a number)
  Add-Ons           → BookEvent.add_ons
  Ref               → BookEvent.reference
  Event Name        → BookEvent.event_name
  Sales Executive   → BookEvent.sales_executive (FK, matched by full name)
  Added Time        → BookEvent.created_at  +  BookDelegate.created_at
  Name              → BookDelegate.first_name + last_name
  Delegate Email    → BookDelegate.email  (+ BookEvent.contact_email for first)
  Direct Line       → BookDelegate.phone_number  (+ BookEvent.contact_phone for first)
  Delegate Number   → BookDelegate.delegate_number
  Attendance - IN?  → BookDelegate.attendance  (via accounts/booking_coercion,
                       so "false" translates to Pending and "Absent" to No-show)

Any invoices that cannot be matched to an Event are written to import_issues.md
in the repo root for manual resolution.

Usage:
    python manage.py import_booking_excel "path/to/file.xlsx"
    python manage.py import_booking_excel "path/to/file.xlsx" --dry-run
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.import_common import parse_import_date, parse_import_datetime
from book_delegate.models import BookDelegate
from book_event.booking_code_canonical import with_default
from book_event.models import BookEvent
from events.models import Event
from event_performance.active_edition_service import (
    extract_year_from_code,
    normalize_master_code,
)

User = get_user_model()

# Every header this command reads. Checked before the import runs, because each
# read is a `.get(header, "")` that turns an absent column into a blank one.
EXPECTED_COLUMNS = (
    "Invoice Number", "Event Code", "Event Name", "Booking Code",
    "Request Date", "Invoice Date", "Date Paid", "Payment Type",
    "Payment Status", "Paid/Free", "Ticket Tier", "Discount", "Add-Ons", "Ref",
    "Delegate Company", "Accounts Contact", "Sales Executive", "Added Time",
    "Name", "Delegate Email", "Direct Line", "Delegate Number",
    "Attendance - IN?",
)


def _s(v) -> str:
    """Any value → stripped string; '' for None / NaN / blank."""
    if v is None or v == "":
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


# Every date this run could not read, collected as it goes and reported in
# import_issues.md alongside the unresolved-event rows. A date is never the
# reason a booking is dropped — pandas returned None here and said nothing, so a
# workbook column written in an unexpected style imported as a column of blanks
# that looked exactly like a column the source had left empty.
_date_warnings: list[str] = []

# Every cell the shared coercion table refused, same treatment as the dates: the
# value is not written, the row is not dropped over one column, and the cell is
# named in import_issues.md rather than disappearing.
_value_warnings: list[str] = []


def _coerced(row, header, field):
    """
    One cell, through accounts/booking_coercion — the table every booking write
    path shares. Returns "" for blank and for a value the column does not store,
    recording the second case in _value_warnings.
    """
    from accounts.booking_coercion import UNSET, coerce

    raw = row.get(header, "")
    value, error = coerce(field, raw)
    if error:
        _value_warnings.append(f"{header}: {error}")
        return ""
    return "" if value is UNSET or value is None else value


def _discount_kwargs(raw):
    """
    The Discount cell as create() keyword arguments.

    A readable percentage or fraction goes to `discount`, the DecimalField; a
    cell that is not a number at all is kept as `discount_code`, which is where
    this command used to put every value including the numeric ones.
    """
    from accounts.booking_coercion import percent_to_fraction

    if not _s(raw):
        return {}
    value, error = percent_to_fraction(raw)
    if error:
        return {"discount_code": _s(raw)}
    return {"discount": value}


def _parse_date(v) -> Optional[Date]:
    """
    A date out of a workbook cell, or None. Never raises.

    Delegates to accounts.import_common.parse_import_date rather than to
    pandas. pandas is lenient, but its leniency is MONTH-FIRST: it reads
    "03/04/2026" as 4 March, while every other date parser in this codebase
    reads it as 3 April. The same slashed date therefore meant two different
    days depending on which importer had loaded it. parse_import_date is
    day-first, matching what is already stored.
    """
    parsed, error = parse_import_date(v)
    if error:
        _date_warnings.append(error)
    return parsed


def _xl_dt(v) -> Optional[datetime]:
    """
    An Excel serial or a date string as a naive datetime. Never raises.

    The serial threshold this used to apply was `f > 40000`, which silently
    accepted any large number in a date column — an id, a row count, an amount —
    and turned it into a date somewhere after 2009. parse_import_datetime bounds
    the serial window at both ends and rejects Excel's phantom 29-Feb-1900.
    """
    parsed, error = parse_import_datetime(v)
    if error:
        _date_warnings.append(error)
    return parsed


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Make a naive datetime timezone-aware using Django's current tz."""
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


class Command(BaseCommand):
    help = "Wipe all booking data and re-import from an Excel workbook."

    def add_arguments(self, parser):
        parser.add_argument("excel_path", type=str, help="Path to the .xlsx file")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview counts and issues without writing to the database.",
        )
        parser.add_argument(
            "--allow-missing-columns",
            action="store_true",
            help=(
                "Import even though a mapped column is absent, leaving it blank "
                "on every row. Off by default; a missing column is an error, "
                "because a blank column and an absent one look identical once "
                "the data is stored."
            ),
        )

    def handle(self, *args, **options):
        path = Path(options["excel_path"])
        dry_run = options["dry_run"]
        prefix = "[DRY RUN] " if dry_run else ""

        if not path.exists():
            raise CommandError(f"File not found: {path}")

        # Module-level, so a second run in the same process would otherwise
        # inherit the first run's unreadable dates and values.
        _date_warnings.clear()
        _value_warnings.clear()

        # ── 1. Load Excel ─────────────────────────────────────────────────────
        self.stdout.write(f"Reading {path} ...")
        df = pd.read_excel(
            str(path),
            dtype=str,
            keep_default_na=False,
            engine="openpyxl",
        )
        self.stdout.write(f"  {len(df):,} rows x {df.shape[1]} columns loaded.")

        # ── 1a. Every mapped column must be PRESENT ───────────────────────────
        # Every read below is `row.get("Header", "")`, so a column this workbook
        # spells differently, or does not carry at all, imports as a column of
        # blanks that is indistinguishable from a column the source left empty.
        # That is not hypothetical. A load of an 11,288-invoice workbook stored
        # paid_or_free as "" on 8,876 invoices and delegate_number as the model
        # default 1 on all 15,180 delegates, because neither column arrived under
        # the header named here; "Paid" never once reached the database, while
        # the model declares Paid and Free as its only valid values. Cross-checked
        # against the source afterwards, 6,204 rows reading Paid were stored as "".
        #
        # import_remaining_bookings already treats a missing header as an error
        # for exactly this reason. This does the same, one release later.
        missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
        if missing:
            message = (
                f"{len(missing)} mapped column(s) are absent from this workbook, "
                "and every row would import them as blank:\n  "
                + "\n  ".join(missing)
                + "\n\nHeaders found:\n  "
                + "\n  ".join(str(c) for c in df.columns)
                + "\n\nRename the columns in the workbook, or pass "
                "--allow-missing-columns to import the rest and leave these empty."
            )
            if not options["allow_missing_columns"]:
                raise CommandError(message)
            self.stdout.write(self.style.WARNING(message))

        # ── 2. Build Event lookup: (master_code, year) → Event ───────────────
        event_lookup: dict[tuple, Event] = {}
        for ev in Event.objects.all():
            mc = normalize_master_code(ev.event_code)
            yr = extract_year_from_code(ev.event_code)
            if (mc, yr) not in event_lookup:
                event_lookup[(mc, yr)] = ev
            # Also index by calendar year from event_date as fallback
            if ev.event_date:
                dy = ev.event_date.year
                if dy != yr and (mc, dy) not in event_lookup:
                    event_lookup[(mc, dy)] = ev

        self.stdout.write(f"  {len(event_lookup)} event (master, year) keys built.")

        # ── 3. Build User lookup: lowercase full name → User ─────────────────
        user_lookup: dict[str, User] = {}
        for u in User.objects.all():
            full = f"{u.first_name} {u.last_name}".strip()
            if full:
                user_lookup[full.lower()] = u
            user_lookup[u.username.lower()] = u

        # ── 4. Group rows by Invoice Number ───────────────────────────────────
        by_invoice: dict[str, list] = defaultdict(list)
        skipped = 0
        for _, row in df.iterrows():
            inv = _s(row.get("Invoice Number", ""))
            if not inv:
                skipped += 1
                continue
            by_invoice[inv].append(row)

        if skipped:
            self.stdout.write(
                self.style.WARNING(f"  Skipped {skipped} rows with no Invoice Number.")
            )
        self.stdout.write(f"  {len(by_invoice):,} unique invoices found.")

        # ── 5. Build BookEvent + BookDelegate objects ──────────────────────────
        events_to_create: list[BookEvent] = []
        delegates_to_create: list[BookDelegate] = []
        issues: list[dict] = []

        for inv_num, rows in by_invoice.items():
            first = rows[0]
            excel_code = _s(first.get("Event Code", ""))
            mc = normalize_master_code(excel_code)
            yr = extract_year_from_code(excel_code)
            event = event_lookup.get((mc, yr))

            if not event:
                issues.append({
                    "invoice_number": inv_num,
                    "excel_code": excel_code,
                    "master": mc or "—",
                    "year": str(yr) if yr else "—",
                    "event_name": _s(first.get("Event Name", "")),
                    "delegate_count": len(rows),
                    "sample_delegates": " | ".join(
                        _s(r.get("Name", "")) for r in rows[:3]
                    ),
                    "reason": f"No Event found for master={mc}, year={yr}",
                })
                continue

            canonical_code = event.event_code

            # ── Sales executive ──────────────────────────────────────────────
            rep_name = _s(first.get("Sales Executive", ""))
            rep: Optional[User] = None
            if rep_name:
                rep = user_lookup.get(rep_name.lower())
                if not rep:
                    # Partial first-name fallback
                    first_token = rep_name.lower().split()[0]
                    for key, u in user_lookup.items():
                        if key.startswith(first_token):
                            rep = u
                            break
                if not rep:
                    issues.append({
                        "invoice_number": inv_num,
                        "excel_code": excel_code,
                        "master": mc or "—",
                        "year": str(yr) if yr else "—",
                        "event_name": _s(first.get("Event Name", "")),
                        "delegate_count": len(rows),
                        "sample_delegates": _s(first.get("Name", "")),
                        "reason": (
                            f"Sales executive '{rep_name}' not found — "
                            "invoice created without rep assignment"
                        ),
                    })

            # ── BookEvent ────────────────────────────────────────────────────
            added_dt = _aware(_xl_dt(_s(first.get("Added Time", ""))))
            contact_name  = _s(first.get("Name", ""))
            contact_email = _s(first.get("Delegate Email", ""))
            contact_phone = _s(first.get("Direct Line", ""))

            be = BookEvent(
                invoice_number         = inv_num,
                event_code             = canonical_code,
                event_name             = _s(first.get("Event Name", "")) or event.name,
                request_date           = _parse_date(_s(first.get("Request Date", ""))),
                invoice_date           = _parse_date(_s(first.get("Invoice Date", ""))),
                # bulk_create() below bypasses BookEvent.save(), so the default
                # the save chokepoint applies has to be applied here too; see
                # book_event/booking_code_canonical.py.
                booking_code           = with_default(_s(first.get("Booking Code", ""))),
                company_name           = _s(first.get("Delegate Company", "")),
                contact_name           = contact_name,
                contact_email          = contact_email,
                contact_phone          = contact_phone,
                accounts_contact_email = _s(first.get("Accounts Contact", "")),
                # THROUGH THE SHARED COERCION TABLE, like every other write path.
                #
                # These four columns were written as raw strings straight off the
                # cell. A CharField with choices is not validated on bulk_create,
                # so a value outside the choice list was stored without complaint
                # and then rendered as a blank cell nobody could explain — which
                # is how paid_or_free came to hold "" on 8,876 invoices and "Paid"
                # on none at all. The check further down this file reported that
                # after the fact; coercing here means the value the workbook
                # states is actually stored, including the spelling the CRM
                # itself displays, "Payable".
                paid_or_free           = _coerced(first, "Paid/Free", "paid_or_free"),
                payment_date           = _parse_date(_s(first.get("Date Paid", ""))),
                payment_type           = _coerced(first, "Payment Type", "payment_type"),
                payment_status         = _coerced(first, "Payment Status", "payment_status") or "Pending",
                ticket_tier            = _coerced(first, "Ticket Tier", "ticket_tier"),
                # The workbook's Discount column holds a NUMBER in the sheets we
                # actually import — "20%" in some rows and "0.2" in others, both
                # meaning the same fraction — and it was being written into
                # discount_CODE, a CharField, so the numeric discount this command
                # imported was never stored at all. It now goes to `discount`
                # where it is a number, and falls back to discount_code for a
                # workbook whose column really does carry a code.
                **_discount_kwargs(first.get("Discount", "")),
                add_ons                = _s(first.get("Add-Ons", "")),
                reference              = _s(first.get("Ref", "")),
                sales_executive        = rep,
                delegate_count         = len(rows),
                source                 = "manual",
            )
            if added_dt:
                be.created_at = added_dt

            events_to_create.append(be)

            # ── BookDelegates ─────────────────────────────────────────────────
            seen_emails: set[str] = set()
            for idx, row in enumerate(rows):
                full_name = _s(row.get("Name", ""))
                parts = full_name.split(None, 1)
                first_name = parts[0] if parts else "Unknown"
                last_name  = parts[1] if len(parts) > 1 else ""

                email = _s(row.get("Delegate Email", ""))
                if not email:
                    email = f"noemail.{idx}@{inv_num.replace('/', '-')}.import"
                # Deduplicate email within same invoice
                base_email = email.lower()
                if base_email in seen_emails:
                    local, domain = email.rsplit("@", 1)
                    email = f"{local}.{idx}@{domain}"
                    base_email = email.lower()
                seen_emails.add(base_email)

                # Through the shared table. This was `"Confirmed" if raw in
                # ("true","1","yes") else "Pending"`, so "false" reached Pending
                # by falling off the end of an if rather than by being
                # translated — and the same fallback silently absorbed No,
                # Absent and anything else the list did not know, flattening
                # "did not appear" onto "not yet known".
                attendance = _coerced(row, "Attendance - IN?", "attendance") or "Pending"

                del_num_s = _s(row.get("Delegate Number", ""))
                try:
                    del_num = int(float(del_num_s)) if del_num_s else 1
                except (ValueError, OverflowError):
                    del_num = 1

                del_dt = _aware(_xl_dt(_s(row.get("Added Time", ""))))

                bd = BookDelegate(
                    invoice_id       = inv_num,   # to_field="invoice_number"
                    event_code       = canonical_code,
                    first_name       = first_name,
                    last_name        = last_name,
                    email            = email,
                    phone_number     = _s(row.get("Direct Line", "")),
                    company_name_raw = _s(row.get("Delegate Company", "")),
                    delegate_number  = del_num,
                    attendance       = attendance,
                    # Same reason as the invoice above: bulk_create() skips
                    # BookDelegate.save(), so neither the inheritance from the
                    # invoice nor the default runs on its own here.
                    booking_code     = be.booking_code,
                )
                if del_dt:
                    bd.created_at = del_dt

                delegates_to_create.append(bd)

        # ── 5a. Values the model would not accept ─────────────────────────────
        # A CharField with choices is not validated on bulk_create, so a value
        # outside the choice list is stored without complaint and then renders as
        # a blank cell nobody can explain. paid_or_free is checked by name rather
        # than in a loop over every field, because it is the one that went wrong
        # and the one the Bookings table resolves through two columns.
        valid_pof = set(BookEvent.PaidOrFree.values)
        bad_pof = defaultdict(int)
        for be in events_to_create:
            if be.paid_or_free not in valid_pof:
                bad_pof[be.paid_or_free] += 1
        if bad_pof:
            total_bad = sum(bad_pof.values())
            self.stdout.write(self.style.WARNING(
                f"{prefix}Paid/Free holds a value the model does not allow on "
                f"{total_bad:,} of {len(events_to_create):,} invoices; the "
                f"Payable / Free column will read blank for them. Allowed values "
                f"are {sorted(valid_pof)}. Found:"
            ))
            for value, count in sorted(bad_pof.items(), key=lambda kv: -kv[1]):
                self.stdout.write(self.style.WARNING(
                    f"  {value!r}  on {count:,} invoice(s)"
                ))
            issues.append({
                "invoice_number": "—",
                "excel_code": "—",
                "master": "—",
                "year": "—",
                "event_name": "—",
                "delegate_count": total_bad,
                "sample_delegates": "; ".join(
                    f"{v!r} x{n}" for v, n in
                    sorted(bad_pof.items(), key=lambda kv: -kv[1])[:5]
                ),
                "reason": (
                    f"Paid/Free outside {sorted(valid_pof)} on {total_bad} "
                    "invoice(s); Payable / Free will read blank for them"
                ),
            })

        # ── 6. Write issues MD ────────────────────────────────────────────────
        md_path = (
            Path(__file__).resolve()
            .parent.parent.parent.parent.parent
            / "import_issues.md"
        )
        if _date_warnings:
            distinct = sorted(set(_date_warnings))
            issues.append({
                "invoice_number": "—",
                "excel_code": "—",
                "master": "—",
                "year": "—",
                "event_name": "—",
                "delegate_count": len(_date_warnings),
                "sample_delegates": "; ".join(distinct[:5]),
                "reason": (
                    f"{len(_date_warnings)} date value(s) across "
                    f"{len(distinct)} distinct spelling(s) could not be read and "
                    "were stored as empty"
                ),
            })
            self.stdout.write(self.style.WARNING(
                f"{prefix}Unreadable dates: {len(_date_warnings)} "
                f"({len(distinct)} distinct) -- stored as empty:"
            ))
            for value in distinct[:20]:
                self.stdout.write(self.style.WARNING(f"  {value}"))
            if len(distinct) > 20:
                self.stdout.write(self.style.WARNING(
                    f"  ... and {len(distinct) - 20} more distinct value(s)."
                ))

        # Same treatment for values the shared coercion table refused. Reported
        # by DISTINCT SPELLING rather than per row, because a workbook that
        # spells one column wrongly spells it wrongly on every row, and 15,180
        # identical lines is not a report anybody reads.
        if _value_warnings:
            distinct_vals = sorted(set(_value_warnings))
            issues.append({
                "invoice_number": "—",
                "excel_code": "—",
                "master": "—",
                "year": "—",
                "event_name": "—",
                "delegate_count": len(_value_warnings),
                "sample_delegates": "; ".join(distinct_vals[:5]),
                "reason": (
                    f"{len(_value_warnings)} cell(s) across "
                    f"{len(distinct_vals)} distinct spelling(s) hold a value the "
                    "column does not store and were left empty"
                ),
            })
            self.stdout.write(self.style.WARNING(
                f"{prefix}Values not recognised: {len(_value_warnings)} "
                f"({len(distinct_vals)} distinct) -- left empty:"
            ))
            for value in distinct_vals[:20]:
                self.stdout.write(self.style.WARNING(f"  {value}"))
            if len(distinct_vals) > 20:
                self.stdout.write(self.style.WARNING(
                    f"  ... and {len(distinct_vals) - 20} more distinct value(s)."
                ))
        if issues:
            lines = [
                "# Booking Import Issues\n\n",
                f"> Generated by `import_booking_excel`. "
                f"**{len(issues)} issue(s)** require manual review.\n\n",
                "| Invoice Number | Event Code | Master | Year | Event Name | Delegates | Reason |\n",
                "|---|---|---|---|---|---|---|\n",
            ]
            for iss in issues:
                safe_name = iss["event_name"].encode("ascii", errors="replace").decode("ascii")
                safe_delegates = iss["sample_delegates"].encode("ascii", errors="replace").decode("ascii")
                lines.append(
                    f"| `{iss['invoice_number']}` "
                    f"| `{iss['excel_code']}` "
                    f"| {iss['master']} "
                    f"| {iss['year']} "
                    f"| {safe_name or '—'} "
                    f"| {iss['delegate_count']} ({safe_delegates}) "
                    f"| {iss['reason']} |\n"
                )
            if not dry_run:
                md_path.write_text("".join(lines), encoding="utf-8")
            self.stdout.write(
                self.style.WARNING(
                    f"{prefix}Issues: {len(issues)} -> {md_path}"
                )
            )

        # ── 7. Summary ────────────────────────────────────────────────────────
        self.stdout.write(
            f"\n{prefix}Invoices   : {len(events_to_create):,}"
        )
        self.stdout.write(
            f"{prefix}Delegates  : {len(delegates_to_create):,}"
        )
        self.stdout.write(
            self.style.WARNING(
                f"{prefix}Issues     : {len(issues)} -- see import_issues.md"
            )
        )

        if dry_run:
            self.stdout.write(self.style.SUCCESS("\nDry run complete -- no changes made."))
            return

        # ── 8. Wipe and re-import in a single transaction ─────────────────────
        self.stdout.write("\nClearing existing booking data...")
        with transaction.atomic():
            BookDelegate.objects.all().delete()
            BookEvent.objects.all().delete()
            self.stdout.write("  Old data cleared.")

            self.stdout.write(f"  Inserting {len(events_to_create):,} invoices...")
            BookEvent.objects.bulk_create(events_to_create, batch_size=500)

            self.stdout.write(f"  Inserting {len(delegates_to_create):,} delegates...")
            BookDelegate.objects.bulk_create(
                delegates_to_create,
                batch_size=500,
                ignore_conflicts=True,
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {len(events_to_create):,} invoices + "
            f"{len(delegates_to_create):,} delegates imported. "
            f"{len(issues)} issues in import_issues.md."
        ))
