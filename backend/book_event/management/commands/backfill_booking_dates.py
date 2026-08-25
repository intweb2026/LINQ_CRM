"""
management command: backfill_booking_dates

Fills the booking date columns that are empty in production from a Zoho-style
export, and writes nothing else. The logic lives in book_event/date_backfill.py;
this is the CLI around it.

    Request Date -> request_date        Payment Due -> payment_due_date
    Invoice Date -> invoice_date        Date Paid   -> payment_date
    and BookDelegate.booked_on, which is COALESCE(request_date, invoice_date)

Blank-only by default: a stored date is never moved, a blank cell never clears
one, and a second run is a no-op. Writes are bulk_update on the named columns, so
none of the save() derivations (event_name, edition, booking_code spelling,
accounts contact) can fire as a side effect.

Usage:
    # always look first
    python manage.py backfill_booking_dates "remaining data.xlsx" --dry-run

    # write, with a record of every change
    python manage.py backfill_booking_dates "remaining data.xlsx" \
        --report date_backfill_report.md

    # one column only
    python manage.py backfill_booking_dates "remaining data.xlsx" \
        --fields payment_due_date

    # replace stored dates that differ from the workbook (NOT the default)
    python manage.py backfill_booking_dates "remaining data.xlsx" --overwrite
"""
from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from book_event.date_backfill import DATE_COLUMNS, backfill_booking_dates


class Command(BaseCommand):
    help = (
        "Fill empty booking date columns (request/invoice/due/paid) from an "
        "Excel export. Blank-only by default; nothing else on the row is touched."
    )

    def add_arguments(self, parser):
        parser.add_argument("excel_path", type=str, help="Path to the .xlsx file")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report every write without touching the database.",
        )
        parser.add_argument(
            "--fields", nargs="+", choices=sorted(DATE_COLUMNS),
            default=sorted(DATE_COLUMNS),
            help="Which date columns to consider. Default: all four.",
        )
        parser.add_argument(
            "--overwrite", action="store_true",
            help=(
                "Replace a stored date that differs from the workbook. Off by "
                "default, so only NULL columns are filled."
            ),
        )
        parser.add_argument(
            "--report", type=str, default="",
            help="Write a per-invoice change report to this path.",
        )

    def handle(self, *args, **opts):
        path = Path(opts["excel_path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        dry = opts["dry_run"]
        prefix = "[DRY RUN] " if dry else ""
        self.stdout.write(f"Reading {path} ...")

        try:
            result = backfill_booking_dates(
                path,
                fields=opts["fields"],
                overwrite=opts["overwrite"],
                dry_run=dry,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        inv = result["invoices"]
        self.stdout.write(
            f"  {inv['in_file']:,} invoices in the workbook; "
            f"{inv['matched']:,} matched in the database."
        )
        if inv["missing_from_db"]:
            self.stdout.write(self.style.WARNING(
                f"  {inv['missing_from_db']:,} invoice number(s) in the workbook "
                f"have no row in this database."
            ))
        if result["unreadable"]:
            self.stdout.write(self.style.WARNING(
                f"  {len(result['unreadable'])} unreadable date value(s) — "
                f"treated as blank:"
            ))
            for value in result["unreadable"][:20]:
                self.stdout.write(self.style.WARNING(f"    {value}"))

        self.stdout.write("")
        self.stdout.write(f"{prefix}Mode: "
                          f"{'overwrite differing dates' if opts['overwrite'] else 'fill NULL only'}")
        width = max(len(f) for f in result["fields"])
        self.stdout.write(f"\n{prefix}{'column':<{width}}  {'filled':>8}{'ok':>9}"
                          f"{'conflict':>10}{'blank':>8}")
        for field in result["fields"]:
            self.stdout.write(
                f"{prefix}{field:<{width}}  "
                f"{result['filled'][field]:>8,}"
                f"{result['already_correct'][field]:>9,}"
                f"{result['conflicts'][field]:>10,}"
                f"{result['blank_in_file'][field]:>8,}"
            )
        total = sum(result["filled"].values())
        self.stdout.write(f"\n{prefix}Dates written    : {total:,}")
        self.stdout.write(f"{prefix}booked_on refreshed: {result['booked_on']:,} delegate(s)")
        if any(result["conflicts"].values()):
            self.stdout.write(self.style.WARNING(
                f"{prefix}Left alone (stored date differs): "
                f"{sum(result['conflicts'].values()):,} — pass --overwrite to replace."
            ))
        if result["varied"]:
            self.stdout.write(self.style.WARNING(
                f"{prefix}{len(result['varied']):,} invoice(s) list more than one "
                f"date across their delegates; the first row's value was used."
            ))

        if opts["report"]:
            self._write_report(Path(opts["report"]), path, result)
            self.stdout.write(f"\nReport -> {opts['report']}")

        if dry:
            self.stdout.write(self.style.SUCCESS("\nDry run complete — nothing written."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone. {total:,} date(s) filled across "
                f"{len({c[0] for c in result['changes']}):,} invoice(s)."
            ))

    def _write_report(self, target: Path, source: Path, result: dict):
        lines = [
            "# Booking date backfill\n\n",
            f"Workbook: `{source}`  \n",
            f"Mode: **{'overwrite' if result['overwrite'] else 'fill NULL only'}**"
            f"{' — DRY RUN' if result['dry_run'] else ''}  \n",
            f"Columns: {', '.join(result['fields'])}  \n\n",
            f"- Invoices in workbook: {result['invoices']['in_file']:,}\n",
            f"- Matched in database: {result['invoices']['matched']:,}\n",
            f"- Not in database: {result['invoices']['missing_from_db']:,}\n",
            f"- Delegates whose `booked_on` was refreshed: {result['booked_on']:,}\n\n",
            "## Per column\n\n",
            "| Column | Filled | Already correct | Stored date differs | Blank in file |\n",
            "|---|---|---|---|---|\n",
        ]
        for field in result["fields"]:
            lines.append(
                f"| {field} | {result['filled'][field]:,} "
                f"| {result['already_correct'][field]:,} "
                f"| {result['conflicts'][field]:,} "
                f"| {result['blank_in_file'][field]:,} |\n"
            )
        if result["missing"]:
            lines.append(f"\n## Invoice numbers not in this database "
                         f"({len(result['missing'])})\n\n")
            for number in result["missing"][:500]:
                lines.append(f"- `{number}`\n")
            if len(result["missing"]) > 500:
                lines.append(f"- … and {len(result['missing']) - 500} more\n")
        if result["varied"]:
            lines.append(
                f"\n## Invoices whose delegates disagree on a date "
                f"({len(result['varied'])})\n\n"
                "The invoice holds one value; the first workbook row's was used.\n\n"
                "| Invoice | Columns that vary |\n|---|---|\n"
            )
            for number, fields in list(result["varied"].items())[:500]:
                lines.append(f"| `{number}` | {', '.join(fields)} |\n")
        if result["unreadable"]:
            lines.append(f"\n## Unreadable cell values ({len(result['unreadable'])})\n\n")
            for value in result["unreadable"]:
                lines.append(f"- `{value}`\n")
        lines.append(f"\n## Changes ({len(result['changes']):,})\n\n"
                     "| Invoice | Column | Stored | Written |\n|---|---|---|---|\n")
        for number, field, old, new in result["changes"]:
            lines.append(f"| `{number}` | {field} | {old or '—'} | {new} |\n")
        target.write_text("".join(lines), encoding="utf-8")
