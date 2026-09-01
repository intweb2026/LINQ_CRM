"""
book_event/tests_update_payment_status.py
──────────────────────────────────────────
Tests for the `update_payment_status` management command.

The subject is the sync rule, which is the only part of this command that can be
wrong in a way a reader of the Bookings table would not notice: Payment Status
is stored in BookEvent.payment_status AND BookDelegate.delegate_payment_status,
and only their combination is what a delegate displays. Each case below asserts
the DISPLAYED value as well as the two columns, so a run that writes one of them
and not the other fails here rather than in production.
"""
import tempfile
from pathlib import Path

import openpyxl
from django.core.management import call_command
from django.test import TestCase

from book_delegate.models import BookDelegate
from book_event.models import BookEvent

HEADERS = ["Payment Status", "Invoice Number", "Name", "Delegate Email"]


def _workbook(rows, headers=HEADERS):
    """A throwaway .xlsx holding `rows`, returned as its path."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(list(row))
    path = Path(tempfile.mkdtemp()) / "statuses.xlsx"
    wb.save(str(path))
    wb.close()
    return str(path)


class UpdatePaymentStatusTests(TestCase):
    def setUp(self):
        self.inv = BookEvent.objects.create(
            invoice_number="PST-001", event_code="TST - 2025",
            payment_status="Pending",
        )
        self.a = self._delegate("Ann", "a@example.com")
        self.b = self._delegate("Bob", "b@example.com")
        self.c = self._delegate("Cid", "c@example.com")

    def _delegate(self, first, email, **kwargs):
        return BookDelegate.objects.create(
            invoice=self.inv, event_code="TST - 2025",
            first_name=first, last_name="Person", email=email, **kwargs
        )

    def _shown(self, delegate):
        """What the CRM displays, the same way every serializer resolves it."""
        delegate.refresh_from_db()
        self.inv.refresh_from_db()
        return delegate.delegate_payment_status or self.inv.payment_status

    def _run(self, rows, *args):
        call_command("update_payment_status", _workbook(rows), "--apply", *args)

    # -- sync -----------------------------------------------------------------
    def test_unanimous_invoice_moves_and_overrides_stay_clear(self):
        self.a.delegate_payment_status = "Pending"
        self.a.save()
        self._run([
            ("Paid", "PST-001", "Ann Person", "a@example.com"),
            ("Paid", "PST-001", "Bob Person", "b@example.com"),
            ("Paid", "PST-001", "Cid Person", "c@example.com"),
        ])
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.payment_status, "Paid")
        # The override that agreed with the invoice is cleared, restoring
        # inheritance — the state the booking modal leaves behind.
        for d in (self.a, self.b, self.c):
            d.refresh_from_db()
            self.assertIsNone(d.delegate_payment_status)
            self.assertEqual(self._shown(d), "Paid")

    def test_mixed_invoice_takes_the_majority_and_the_odd_one_gets_an_override(self):
        self._run([
            ("Paid", "PST-001", "Ann Person", "a@example.com"),
            ("Paid", "PST-001", "Bob Person", "b@example.com"),
            ("Cancelled", "PST-001", "Cid Person", "c@example.com"),
        ])
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.payment_status, "Paid")
        self.c.refresh_from_db()
        self.assertEqual(self.c.delegate_payment_status, "Cancelled")
        self.assertEqual(self._shown(self.a), "Paid")
        self.assertEqual(self._shown(self.c), "Cancelled")
        # BookDelegate.save() forces this, and bulk_update does not run save(),
        # so the command applies the same rule by hand.
        self.assertEqual(self.c.delegate_count, 0)
        self.assertEqual(self.a.delegate_count, 1)

    def test_partial_coverage_leaves_the_invoice_alone(self):
        # One of three delegates named, so what the invoice should say is not
        # knowable from the file; moving it would re-label Bob and Cid.
        self._run([("Paid", "PST-001", "Ann Person", "a@example.com")])
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.payment_status, "Pending")
        self.assertEqual(self._shown(self.a), "Paid")
        self.assertEqual(self._shown(self.b), "Pending")

    def test_leaving_cancelled_restores_delegate_count(self):
        self.c.delegate_payment_status = "Cancelled"
        self.c.save()
        self.assertEqual(self.c.delegate_count, 0)
        self._run([
            ("Paid", "PST-001", "Ann Person", "a@example.com"),
            ("Paid", "PST-001", "Bob Person", "b@example.com"),
            ("Paid", "PST-001", "Cid Person", "c@example.com"),
        ])
        self.c.refresh_from_db()
        self.assertIsNone(self.c.delegate_payment_status)
        self.assertEqual(self.c.delegate_count, 1)

    # -- refusals -------------------------------------------------------------
    def test_a_status_off_the_choice_list_is_refused(self):
        self._run([
            ("Banana", "PST-001", "Ann Person", "a@example.com"),
            ("Paid", "PST-001", "Bob Person", "b@example.com"),
            ("Paid", "PST-001", "Cid Person", "c@example.com"),
        ])
        # Ann is not counted at all, so the invoice sees partial coverage and is
        # left where it was rather than moved on two rows out of three.
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.payment_status, "Pending")
        self.assertEqual(self._shown(self.b), "Paid")

    def test_a_blank_cell_writes_nothing(self):
        self.a.delegate_payment_status = "Paid"
        self.a.save()
        self._run([("", "PST-001", "Ann Person", "a@example.com")])
        self.a.refresh_from_db()
        self.assertEqual(self.a.delegate_payment_status, "Paid")

    def test_allow_clear_clears_the_override(self):
        self.a.delegate_payment_status = "Paid"
        self.a.save()
        self._run([("", "PST-001", "Ann Person", "a@example.com")], "--allow-clear")
        self.a.refresh_from_db()
        self.assertIsNone(self.a.delegate_payment_status)

    def test_a_headerless_sheet_is_refused_rather_than_eating_a_row(self):
        from django.core.management.base import CommandError
        path = _workbook(
            [("Paid", "PST-001", "Bob Person", "b@example.com")],
            headers=["Paid", "PST-001", "Ann Person", "a@example.com"],
        )
        with self.assertRaises(CommandError):
            call_command("update_payment_status", path, "--apply")
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.payment_status, "Pending")
