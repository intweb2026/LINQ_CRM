"""
sync_bookings_from_sheets.py
────────────────────────────────────────────────────────────────────
Pull booking rows from a Google Sheet and upsert into BookEvent.
Matching key : invoice_number  (unique=True in the model)
Safety rules : update_or_create only — .delete() is never called.
Atomicity    : all DB writes are wrapped in a single transaction.atomic().
               If any write fails the entire batch rolls back so the DB
               is never left in a partially-synced state.
"""
import os
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.import_common import parse_import_date
from book_event.models import BookEvent


SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

DATE_FIELDS = {
    "event_date", "invoice_date", "payment_date",
    "payment_due_date", "request_date",
}
NULLABLE_DECIMAL_FIELDS = {
    "pre_tax_amount", "tax_amount", "total_amount", "add_ons_total_amount",
}
ZERO_DEFAULT_DECIMAL_FIELDS = {"discount"}
INT_FIELDS = {"delegate_count", "edition"}

# Supported sheet column headers and the BookEvent field they map to.
# The sheet header row must use these exact names (case-sensitive).
# Columns not in this map are silently ignored.
COLUMN_MAP = {
    "invoice_number":         "invoice_number",
    "event_code":             "event_code",
    "event_name":             "event_name",
    "event_date":             "event_date",
    "invoice_date":           "invoice_date",
    "booking_code":           "booking_code",
    "company_name":           "company_name",
    "contact_name":           "contact_name",
    "contact_email":          "contact_email",
    "contact_phone":          "contact_phone",
    "accounts_contact_email": "accounts_contact_email",
    "discount":               "discount",
    "discount_code":          "discount_code",
    "pre_tax_amount":         "pre_tax_amount",
    "tax_amount":             "tax_amount",
    "total_amount":           "total_amount",
    "add_ons_total_amount":   "add_ons_total_amount",
    "currency":               "currency",
    "ticket_tier":            "ticket_tier",
    "delegate_count":         "delegate_count",
    "source":                 "source",
    "form_name":              "form_name",
    "form_url":               "form_url",
    "payment_status":         "payment_status",
    "payment_date":           "payment_date",
    "payment_due_date":       "payment_due_date",
    "payment_type":           "payment_type",
    "paid_or_free":           "paid_or_free",
    "reference":              "reference",
    "parent_code":            "parent_code",
    "request_date":           "request_date",
    "notes":                  "notes",
    "add_ons":                "add_ons",
    "attendance":             "attendance",
}


def _parse_date(value, warnings=None):
    """
    A date out of a sheet cell, or None. Never raises.

    The five hardcoded formats this used to carry are gone; the format list is
    accounts.import_common.parse_import_date, which this codebase already
    declares the single authority on reading a date out of an import. Keeping a
    private list here is how the sheet sync came to accept a different set of
    formats from the webhook and from the two file importers, for the same
    columns of the same table.

    An unrecognised value appends its reason to `warnings` rather than being
    dropped in silence. A cell that is genuinely blank appends nothing — that is
    the sheet saying "no date", not a parse failure.
    """
    parsed, error = parse_import_date(value)
    if error and warnings is not None:
        warnings.append(error)
    return parsed


def _parse_decimal(value, nullable=True):
    if not value or not str(value).strip():
        return None if nullable else Decimal("0")
    cleaned = str(value).strip().replace(",", "").lstrip("$£€")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None if nullable else Decimal("0")


def _parse_int(value, default=None):
    if not value or not str(value).strip():
        return default
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return default


def _build_defaults(row, active_headers, date_warnings=None, value_warnings=None):
    """
    Convert a sheet row dict into a BookEvent defaults dict.
    Only includes columns present in active_headers; invoice_number is excluded
    (it is the lookup key, not a default).

    CONSTRAINED COLUMNS GO THROUGH THE SHARED COERCION TABLE.
    Payment Status, Payable/Free, Payment Type, Ticket Tier, Currency, Attendance,
    Discount, Delegate Count and Edition were read here as `str(raw).strip()`, a
    bare `Decimal()` or a `_parse_int(..., default=1)`. A CharField with choices
    is not validated on save(), so a spelling the model does not declare was
    stored verbatim and then rendered as a blank cell nobody could explain, and
    the delegate-count default of 1 rewrote a stated zero exactly as the browser
    importer's max(1, ...) did. accounts/booking_coercion is now the one authority
    for all of them, shared with the browser import, the two commands and the
    website intake.

    A value it refuses is left OUT of the defaults, so the stored value survives
    rather than being overwritten with a blank, and the cell is named in
    `value_warnings` for the caller to report.
    """
    from accounts.booking_coercion import RULES, UNSET, coerce

    defaults = {}
    for col_header, field_name in COLUMN_MAP.items():
        if col_header == "invoice_number" or col_header not in active_headers:
            continue
        raw = row.get(col_header, "")
        if field_name in RULES:
            value, error = coerce(field_name, raw)
            if error:
                if value_warnings is not None:
                    value_warnings.append(f"{col_header}: {error}")
                continue
            if value is UNSET:
                continue
            defaults[field_name] = value
        elif field_name in DATE_FIELDS:
            defaults[field_name] = _parse_date(raw, date_warnings)
        elif field_name in NULLABLE_DECIMAL_FIELDS:
            defaults[field_name] = _parse_decimal(raw, nullable=True)
        elif field_name in ZERO_DEFAULT_DECIMAL_FIELDS:
            defaults[field_name] = _parse_decimal(raw, nullable=False)
        elif field_name in INT_FIELDS:
            int_default = 1 if field_name == "delegate_count" else None
            defaults[field_name] = _parse_int(raw, default=int_default)
        else:
            defaults[field_name] = str(raw).strip() if raw else ""
    return defaults


class Command(BaseCommand):
    help = "Sync booking rows from Google Sheets into BookEvent (upsert only — never deletes)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Read the sheet and report what would be created/updated "
                "without writing anything to the database."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # ── Load config from environment (.env is loaded by Django settings) ──
        sheet_id   = os.environ.get("GOOGLE_SHEET_ID", "").strip()
        sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "Sheet1").strip()
        creds_path = os.environ.get("GOOGLE_CREDS_PATH", "credentials.json").strip()

        if not sheet_id:
            raise CommandError(
                "GOOGLE_SHEET_ID is not set. Add it to your .env file."
            )
        if not os.path.isfile(creds_path):
            raise CommandError(
                f"Service-account credentials file not found: '{creds_path}'\n"
                "Set GOOGLE_CREDS_PATH in .env to the correct path."
            )

        # ── Authenticate with Google Sheets (read-only scope) ──────────────────
        # Imported HERE rather than at module scope so the pure parsing helpers
        # above — _build_defaults and the coercion it shares with every other
        # booking write path — can be imported and tested without the Google
        # client installed. A missing network dependency should not make a
        # column-mapping rule untestable.
        import gspread

        self.stdout.write("Authenticating with Google Sheets (read-only)...")
        try:
            client = gspread.service_account(filename=creds_path, scopes=SCOPES)
        except Exception as exc:
            raise CommandError(f"Google authentication failed: {exc}")

        # ── Open the spreadsheet / worksheet ───────────────────────────────────
        self.stdout.write(f"Opening sheet ID '{sheet_id}', tab '{sheet_name}' ...")
        try:
            spreadsheet = client.open_by_key(sheet_id)
            worksheet   = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.SpreadsheetNotFound:
            raise CommandError(
                "Spreadsheet not found. Check GOOGLE_SHEET_ID and that the "
                "service-account email has been given Viewer access to the sheet."
            )
        except gspread.exceptions.WorksheetNotFound:
            raise CommandError(
                f"Tab '{sheet_name}' not found. Check GOOGLE_SHEET_NAME in .env."
            )
        except Exception as exc:
            raise CommandError(f"Could not open spreadsheet: {exc}")

        # ── Fetch all rows as a list of dicts ──────────────────────────────────
        rows = worksheet.get_all_records(head=1)
        if not rows:
            self.stdout.write(self.style.WARNING("Sheet is empty — nothing to sync."))
            return

        active_headers = set(rows[0].keys())
        self.stdout.write(
            f"Read {len(rows)} data row(s). "
            f"Columns found: {', '.join(sorted(active_headers))}"
        )

        if "invoice_number" not in active_headers:
            raise CommandError(
                "Required column 'invoice_number' not found in the sheet header row. "
                "Rename the column in your sheet to exactly: invoice_number"
            )

        # ── Phase 1: Parse all rows (no DB access yet) ─────────────────────────
        parsed_rows  = []
        skipped      = 0
        parse_errors = 0
        # Dates that could not be read. These do NOT make the row a parse error;
        # the rest of the booking is still worth syncing. They are reported at
        # the end so an unreadable date column cannot pass for an empty one.
        date_warnings = []
        # Values the shared coercion table refused. Same rule as the dates: the
        # column is left as it is stored rather than overwritten with a blank, the
        # row still syncs, and the cell is named at the end so a sheet spelling
        # this system does not know cannot pass for a clean sync.
        value_warnings = []

        for row_num, row in enumerate(rows, start=2):  # row 1 is the header
            invoice_number = str(row.get("invoice_number", "")).strip()
            if not invoice_number:
                self.stdout.write(
                    self.style.WARNING(f"  Row {row_num}: skipped — empty invoice_number")
                )
                skipped += 1
                continue
            try:
                defaults = _build_defaults(
                    row, active_headers, date_warnings, value_warnings)
                parsed_rows.append((row_num, invoice_number, defaults))
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"  Row {row_num}: parse error — {exc}")
                )
                parse_errors += 1

        self.stdout.write(
            f"\nParse result: {len(parsed_rows)} valid | "
            f"{skipped} skipped (no invoice_number) | "
            f"{parse_errors} parse error(s)"
        )

        if date_warnings:
            distinct = sorted(set(date_warnings))
            self.stdout.write(self.style.WARNING(
                f"  {len(date_warnings)} date value(s) unreadable, stored as empty:"
            ))
            for value in distinct[:20]:
                self.stdout.write(self.style.WARNING(f"    {value}"))
            if len(distinct) > 20:
                self.stdout.write(self.style.WARNING(
                    f"    ... and {len(distinct) - 20} more distinct value(s)."
                ))

        if value_warnings:
            distinct_vals = sorted(set(value_warnings))
            self.stdout.write(self.style.WARNING(
                f"  {len(value_warnings)} value(s) not recognised, left unchanged:"
            ))
            for value in distinct_vals[:20]:
                self.stdout.write(self.style.WARNING(f"    {value}"))
            if len(distinct_vals) > 20:
                self.stdout.write(self.style.WARNING(
                    f"    ... and {len(distinct_vals) - 20} more distinct value(s)."
                ))

        if not parsed_rows:
            self.stdout.write(self.style.WARNING("No valid rows to process."))
            self._print_summary(len(rows), 0, 0, skipped, parse_errors, dry_run)
            return

        # ── Dry-run: check DB state, write nothing ─────────────────────────────
        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY RUN] No data will be written.\n"))
            existing = set(
                BookEvent.objects.filter(
                    invoice_number__in=[r[1] for r in parsed_rows]
                ).values_list("invoice_number", flat=True)
            )
            would_create = sum(1 for _, inv, _ in parsed_rows if inv not in existing)
            would_update = sum(1 for _, inv, _ in parsed_rows if inv in existing)
            self._print_summary(
                len(rows), would_create, would_update, skipped, parse_errors, dry_run
            )
            return

        # ── Phase 2: Upsert in a single atomic transaction ─────────────────────
        # If any write fails, the whole batch rolls back and CommandError is raised.
        self.stdout.write("Writing to database...")
        created_count = 0
        updated_count = 0

        with transaction.atomic():
            for row_num, invoice_number, defaults in parsed_rows:
                _, created = BookEvent.objects.update_or_create(
                    invoice_number=invoice_number,
                    defaults=defaults,
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        self._print_summary(
            len(rows), created_count, updated_count, skipped, parse_errors, dry_run
        )

    def _print_summary(self, total_read, created, updated, skipped, parse_errors, dry_run):
        label = "[DRY RUN] " if dry_run else ""
        self.stdout.write("\n" + "-" * 52)
        self.stdout.write(self.style.SUCCESS(f"{label}Sync Complete"))
        self.stdout.write(f"  Sheet rows read  : {total_read}")
        self.stdout.write(f"  Skipped          : {skipped}  (empty invoice_number)")
        self.stdout.write(f"  Parse errors     : {parse_errors}")
        if dry_run:
            self.stdout.write(f"  Would create     : {created}")
            self.stdout.write(f"  Would update     : {updated}")
        else:
            self.stdout.write(f"  Created          : {created}")
            self.stdout.write(f"  Updated          : {updated}")
        self.stdout.write("-" * 52)
