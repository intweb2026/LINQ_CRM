"""
book_event/tests_import_booking_excel_columns.py
────────────────────────────────────────────────
`manage.py import_booking_excel` — the missing-column and bad-value guards.

THE BUG THIS CLOSES
Every column this importer reads is read as `row.get("Header", "")`, so a
workbook that spells a header differently, or does not carry it at all, imports
that column as blanks on every single row. A blank column and an absent column
are indistinguishable once the data is stored, and the import reports success.

That is not hypothetical. A load of an 11,288-invoice workbook left
BookEvent.paid_or_free as "" on 8,876 invoices and BookDelegate.delegate_number
at the model default of 1 on all 15,180 delegates. Cross-checked against the
source afterwards, 6,204 rows reading Paid were stored as ""; the value "Paid"
was absent from the entire table, while the model declares Paid and Free as its
only valid values, so the Bookings table showed a blank Payable / Free for two
thirds of the database and nothing anywhere said why.

WHAT THIS PINS
1.  A missing mapped column is an ERROR, and the error names the column and
    lists the headers that were found, so the fix is obvious from the message.
2.  Nothing is written when that error fires. The importer wipes before it
    inserts, so a run that aborts halfway on a bad workbook would be the worst
    outcome available.
3.  --allow-missing-columns is the deliberate escape hatch, and it warns.
4.  A Paid/Free value outside BookEvent.PaidOrFree is reported. Choices are not
    enforced by bulk_create, so this is the only thing standing between a
    misspelled cell and a column that silently reads blank.

    python manage.py test book_event.tests_import_booking_excel_columns
"""
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import mkdtemp

import openpyxl
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from book_delegate.models import BookDelegate
from book_event.management.commands.import_booking_excel import EXPECTED_COLUMNS
from book_event.models import BookEvent
from events.models import Event

EVENT_CODE = "IBE - AA 26"


def write_workbook(path: Path, rows, drop=()):
    """A workbook carrying every expected column, minus any named in `drop`."""
    headers = [c for c in EXPECTED_COLUMNS if c not in drop]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])
    wb.save(str(path))
    return path


class ImportBookingExcelColumnGuardTests(TestCase):

    def setUp(self):
        self.tmp = Path(mkdtemp(prefix="ibe-"))
        self.event = Event.objects.create(
            event_code=EVENT_CODE, name="Import Guard Event 2026",
            event_date=date(2026, 6, 1),
        )
        # A pre-existing booking, so "nothing was written" can be asserted as
        # "the old data is still there"; this importer wipes before it inserts.
        self.survivor = BookEvent.objects.create(
            invoice_number="INV-SURVIVOR", event_code="IBE - AA",
            edition=2026, payment_status="Paid", currency="USD",
        )

    def run_import(self, rows, drop=(), *args):
        book = write_workbook(self.tmp / "book.xlsx", rows, drop=drop)
        out = StringIO()
        call_command(
            "import_booking_excel", str(book), *args, stdout=out, stderr=out,
        )
        return out.getvalue()

    def good_row(self, **overrides):
        row = {
            "Invoice Number": "INV-IBE-1",
            "Event Code": EVENT_CODE,
            "Event Name": "Import Guard Event 2026",
            "Name": "Ada Lovelace",
            "Delegate Email": "ada@acme.test",
            "Paid/Free": "Paid",
            "Delegate Number": 1,
            "Payment Status": "Paid",
        }
        row.update(overrides)
        return row

    # -- 1 and 2, a missing column is an error and writes nothing -------------
    def test_missing_column_is_an_error_naming_the_column(self):
        with self.assertRaises(CommandError) as caught:
            self.run_import([self.good_row()], drop=("Paid/Free",))
        message = str(caught.exception)
        self.assertIn("Paid/Free", message)
        self.assertIn("would import them as blank", message)
        # The headers that ARE present are listed, so the fix is readable off
        # the error rather than guessed at.
        self.assertIn("Invoice Number", message)
        self.assertIn("--allow-missing-columns", message)

    def test_missing_column_writes_nothing(self):
        with self.assertRaises(CommandError):
            self.run_import([self.good_row()], drop=("Delegate Number",))
        # The wipe never ran, so the booking that was already there survives.
        self.assertTrue(
            BookEvent.objects.filter(invoice_number="INV-SURVIVOR").exists()
        )
        self.assertFalse(
            BookEvent.objects.filter(invoice_number="INV-IBE-1").exists()
        )

    def test_every_expected_column_present_is_accepted(self):
        out = self.run_import([self.good_row()], (), "--dry-run")
        self.assertNotIn("absent from this workbook", out)

    # -- 3, the escape hatch -------------------------------------------------
    def test_allow_missing_columns_warns_and_proceeds(self):
        out = self.run_import(
            [self.good_row()], ("Paid/Free",),
            "--dry-run", "--allow-missing-columns",
        )
        self.assertIn("absent from this workbook", out)
        self.assertIn("Paid/Free", out)
        # And it says the consequence, rather than only that a column is gone.
        self.assertIn("does not allow", out)

    # -- 4, a value the model would not accept -------------------------------
    def test_unusable_paid_or_free_value_is_reported(self):
        out = self.run_import(
            [self.good_row(**{"Paid/Free": "Payable"})], (), "--dry-run",
        )
        self.assertIn("Paid/Free holds a value the model does not allow", out)
        self.assertIn("'Payable'", out)
        self.assertIn("['Free', 'Paid']", out)

    def test_blank_paid_or_free_is_reported_too(self):
        # The exact state the bad load left behind, on 8,876 invoices.
        out = self.run_import(
            [self.good_row(**{"Paid/Free": ""})], (), "--dry-run",
        )
        self.assertIn("Paid/Free holds a value the model does not allow", out)
        self.assertIn("read blank", out)

    def test_a_valid_value_is_not_reported(self):
        out = self.run_import([self.good_row()], (), "--dry-run")
        self.assertNotIn("does not allow", out)

    # -- the column that started this ----------------------------------------
    def test_delegate_number_zero_survives_the_import(self):
        self.run_import([self.good_row(**{"Delegate Number": 0})])
        delegate = BookDelegate.objects.get(invoice_id="INV-IBE-1")
        self.assertEqual(delegate.delegate_number, 0)
        self.assertEqual(
            BookEvent.objects.get(invoice_number="INV-IBE-1").paid_or_free,
            "Paid",
        )
