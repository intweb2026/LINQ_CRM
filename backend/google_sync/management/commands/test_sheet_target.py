"""
Test the Sheet Sync Target pipeline end-to-end from the command line.

Usage (from backend/):
    python manage.py test_sheet_target
    python manage.py test_sheet_target --dry-run
    python manage.py test_sheet_target --list-modules

Steps:
  1. Validates credentials file exists
  2. Lists available catalog modules and their columns
  3. Builds rows from the catalog for the chosen module and columns
  4. Connects to the target spreadsheet using GoogleSheetsService
  5. Ensures the target tab exists
  6. Writes rows via replace_data_chunked
  7. Reports success or the exact error

Does NOT create a SheetSyncTarget DB record. Exercises the same code
path _execute_sheet_target uses.
"""
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


DEFAULT_SHEET_ID = "1zELHh58Ld8uJDPIXFrRf3WjWxDv8SyYstDYASe-FwAQ"
DEFAULT_TAB      = "Sheet1"
DEFAULT_MODULE   = "bookings"
DEFAULT_COLUMNS  = ["payment_status", "invoice_number", "event_code",
                     "delegate_name", "delegate_company"]


class Command(BaseCommand):
    help = "Test the sheet-target sync pipeline without the UI."

    def add_arguments(self, parser):
        parser.add_argument("--sheet-id", default=DEFAULT_SHEET_ID,
                            help="Spreadsheet ID or full URL.")
        parser.add_argument("--tab", default=DEFAULT_TAB,
                            help="Tab to write into.")
        parser.add_argument("--module", default=DEFAULT_MODULE,
                            help="Catalog module key.")
        parser.add_argument("--columns", nargs="*", default=DEFAULT_COLUMNS,
                            help="Column keys to push.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Build rows but do not write to Google Sheets.")
        parser.add_argument("--list-modules", action="store_true",
                            help="Print the catalog and exit.")

    def handle(self, *args, **options):
        creds = getattr(settings, "GOOGLE_SHEETS_CREDENTIALS", "")
        self.stdout.write(f"\n[1/6] Credentials path: {creds}")
        if not creds or not os.path.exists(creds):
            raise CommandError(
                f"Credentials file not found at '{creds}'.\n"
                "Set GOOGLE_SHEETS_CREDENTIALS in backend/.env."
            )
        self.stdout.write(self.style.SUCCESS("  PASS  Credentials file exists."))

        from sync.catalog import list_modules, columns_for, build_rows, CatalogError

        self.stdout.write(f"\n[2/6] Loading catalog...")
        modules = list_modules()
        self.stdout.write(f"  {len(modules)} module(s) available:")
        for m in modules:
            cols = [c["key"] for c in m["columns"]]
            self.stdout.write(f"    {m['key']:<25} {len(cols)} columns")

        if options["list_modules"]:
            self.stdout.write("\n  Full column lists:\n")
            for m in modules:
                self.stdout.write(f"\n  {m['key']} ({m['label']}):")
                for c in m["columns"]:
                    self.stdout.write(f"    {c['key']:<35} {c['label']}")
            return

        module  = options["module"]
        columns = options["columns"]

        self.stdout.write(f"\n  Module:  {module}")
        self.stdout.write(f"  Columns: {columns}")

        try:
            available = {c["key"] for c in columns_for(module)}
        except CatalogError as exc:
            raise CommandError(str(exc))

        bad = [c for c in columns if c not in available]
        if bad:
            raise CommandError(
                f"Unknown column(s) for '{module}': {bad}\n"
                f"Available: {sorted(available)}"
            )
        self.stdout.write(self.style.SUCCESS("  PASS  Module and columns valid."))

        self.stdout.write(f"\n[3/6] Building rows...")
        start = time.time()
        try:
            headers, row_iter = build_rows(module, columns)
        except Exception as exc:
            raise CommandError(f"build_rows failed: {exc}")

        rows = list(row_iter)
        elapsed = time.time() - start
        self.stdout.write(self.style.SUCCESS(
            f"  PASS  {len(rows)} row(s) x {len(headers)} col(s) in {elapsed:.2f}s."
        ))
        self.stdout.write(f"  Headers: {headers}")
        if rows:
            self.stdout.write(f"  First row: {rows[0]}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(
                "\n  --dry-run: skipping Google Sheets write.\n"
            ))
            return

        sheet_id = options["sheet_id"]
        tab      = options["tab"]
        self.stdout.write(f"\n[4/6] Connecting to spreadsheet: {sheet_id}")

        from services.google_sheets import GoogleSheetsService
        try:
            service = GoogleSheetsService(spreadsheet_id=sheet_id)
        except Exception as exc:
            raise CommandError(f"GoogleSheetsService init failed: {exc}")
        self.stdout.write(self.style.SUCCESS(
            f"  PASS  Connected. Resolved ID: {service.spreadsheet_id}"
        ))

        self.stdout.write(f"\n[5/6] Listing tabs in spreadsheet...")
        try:
            existing_tabs = service.list_tabs()
            self.stdout.write(f"  Existing tabs: {existing_tabs}")
            service.ensure_tabs([tab])
            self.stdout.write(self.style.SUCCESS(f"  PASS  Tab '{tab}' ready."))
        except Exception as exc:
            raise CommandError(f"Tab operation failed: {exc}")

        self.stdout.write(f"\n[6/6] Writing {len(rows)} rows to {tab}...")
        start = time.time()
        try:
            count = service.replace_data_chunked(tab, headers, iter(rows))
        except Exception as exc:
            raise CommandError(f"Write failed: {exc}")

        elapsed = time.time() - start
        self.stdout.write(self.style.SUCCESS(
            f"\n  ALL PASSED. Wrote {count} rows to '{tab}' in {elapsed:.2f}s.\n"
            f"  Open the sheet to verify:\n"
            f"  https://docs.google.com/spreadsheets/d/{service.spreadsheet_id}/\n"
        ))
