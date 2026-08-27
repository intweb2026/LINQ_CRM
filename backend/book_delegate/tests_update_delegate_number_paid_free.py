"""
book_delegate/tests_update_delegate_number_paid_free.py
───────────────────────────────────────────────────────
`manage.py update_delegate_number_paid_free` — the two-column Excel update.

WHAT THIS PINS, AND WHY EACH ONE MATTERS
1.  A dry run writes NOTHING. Dry run is the default mode, so a person looking
    at what the workbook would do must not have already done it.
2.  Only delegate_number and delegate_paid_or_free move. Every other column on
    a written row is asserted field by field, because BookDelegate.save()
    rewrites event_code, booking_code, booked_on, delegate_count and the
    invoice's accounts contact email, and a command that reached save() would
    change all of those while the counts still looked right.
3.  A delegate the workbook never mentions is asserted field by field too, so a
    stray queryset update cannot pass unnoticed.
4.  Paid/Free is compared against the value the Bookings table DISPLAYS, which
    is the delegate override when set and the invoice's value otherwise. A row
    that already displays the workbook's value is not rewritten; without this
    the command would stamp an override on essentially every row in the
    database and call it a correction.
5.  Matching survives the spelling differences a real export has; invoice case
    and spacing, and a middle initial written with or without its full stop.
6.  A blank cell is skipped, never written as a blank.
7.  A workbook row that matches nothing is reported and never created; this
    command updates, it does not import.
8.  A name that picks out two stored delegates on one invoice is left alone.

    python manage.py test book_delegate.tests_update_delegate_number_paid_free
"""
from io import StringIO
from pathlib import Path
from tempfile import mkdtemp

import openpyxl
from django.core.management import call_command
from django.test import TestCase

from book_delegate.models import BookDelegate
from book_event.models import BookEvent

EVENT_CODE = "UDN - AA"

HEADERS = ["Invoice Number", "Name", "Delegate Number", "Paid/Free",
           "Delegate Email"]


def write_workbook(path: Path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for row in rows:
        ws.append(list(row) + [""] * (len(HEADERS) - len(row)))
    wb.save(str(path))
    return path


class UpdateDelegateNumberPaidFreeBase(TestCase):
    """
    Two invoices. INV-UDN-1 is Paid at the invoice level and carries Ada and
    Bob; INV-UDN-2 carries the control delegate no workbook here mentions.
    """

    def setUp(self):
        self.tmp = Path(mkdtemp(prefix="udn-"))

        self.invoice = BookEvent.objects.create(
            invoice_number="INV-UDN-1", event_code=EVENT_CODE, edition=2026,
            payment_status="Paid", paid_or_free="Paid", booking_code="Speaker",
            company_name="Acme", currency="USD",
        )
        self.other_invoice = BookEvent.objects.create(
            invoice_number="INV-UDN-2", event_code=EVENT_CODE, edition=2026,
            payment_status="Paid", paid_or_free="Free", booking_code="Speaker",
            company_name="Globex", currency="USD",
        )
        self.ada = BookDelegate.objects.create(
            invoice=self.invoice, event_code=EVENT_CODE, edition=2026,
            first_name="Ada", last_name="Lovelace", email="ada@acme.test",
            delegate_number=1, position="CTO", notes="keep me",
        )
        self.bob = BookDelegate.objects.create(
            invoice=self.invoice, event_code=EVENT_CODE, edition=2026,
            first_name="Bob", last_name="Stone", email="bob@acme.test",
            delegate_number=1, position="Engineer",
        )
        self.control = BookDelegate.objects.create(
            invoice=self.other_invoice, event_code=EVENT_CODE, edition=2026,
            first_name="Cat", last_name="Moss", email="cat@globex.test",
            delegate_number=1, position="Analyst",
        )

    def run_command(self, rows, *args):
        book = write_workbook(self.tmp / "sheet.xlsx", rows)
        out = StringIO()
        call_command(
            "update_delegate_number_paid_free", str(book),
            *args, stdout=out, stderr=out,
        )
        return out.getvalue()

    def assert_untouched(self, delegate, delegate_number=1):
        """Every column of a delegate no workbook row should have written."""
        fresh = BookDelegate.objects.get(pk=delegate.pk)
        self.assertEqual(fresh.delegate_number, delegate_number)
        self.assertIsNone(fresh.delegate_paid_or_free)
        self.assertEqual(fresh.first_name, delegate.first_name)
        self.assertEqual(fresh.last_name, delegate.last_name)
        self.assertEqual(fresh.email, delegate.email)
        self.assertEqual(fresh.event_code, delegate.event_code)
        self.assertEqual(fresh.edition, delegate.edition)
        self.assertEqual(fresh.booking_code, delegate.booking_code)
        self.assertEqual(fresh.delegate_count, delegate.delegate_count)
        self.assertEqual(fresh.position, delegate.position)
        self.assertEqual(fresh.notes, delegate.notes)
        self.assertEqual(fresh.attendance, delegate.attendance)
        self.assertEqual(fresh.booked_on, delegate.booked_on)


class DryRunTests(UpdateDelegateNumberPaidFreeBase):

    def test_dry_run_writes_nothing(self):
        out = self.run_command([("INV-UDN-1", "Ada Lovelace", 0, "Free")])
        self.assertIn("DRY RUN", out)
        self.assert_untouched(self.ada)
        self.assert_untouched(self.bob)
        self.assert_untouched(self.control)

    def test_dry_run_counts_the_change_it_would_make(self):
        out = self.run_command([("INV-UDN-1", "Ada Lovelace", 0, "Free")])
        self.assertIn("delegate_number to change       : 1", out)
        # Ada is the only workbook row on an invoice that also carries Bob, so
        # Free cannot go on the shared invoice; it is a genuine per-delegate
        # difference and lands as one override.
        self.assertIn("invoice paid_or_free to change  : 0", out)
        self.assertIn("delegate override to change     : 1", out)


class ApplyTests(UpdateDelegateNumberPaidFreeBase):

    def test_writes_both_columns_and_only_those(self):
        # Both delegates on the invoice, so the Payable / Free value settles on
        # the invoice and Ada's override is not needed; Ada's delegate_number
        # still moves.
        self.run_command(
            [
                ("INV-UDN-1", "Ada Lovelace", 0, "Paid"),
                ("INV-UDN-1", "Bob Stone", 1, "Paid"),
            ],
            "--apply",
        )

        ada = BookDelegate.objects.get(pk=self.ada.pk)
        self.assertEqual(ada.delegate_number, 0)
        self.assertIsNone(ada.delegate_paid_or_free)
        # Nothing else moved. save() would have rewritten several of these.
        self.assertEqual(ada.event_code, EVENT_CODE)
        self.assertEqual(ada.edition, 2026)
        self.assertEqual(ada.booking_code, self.ada.booking_code)
        self.assertEqual(ada.delegate_count, self.ada.delegate_count)
        self.assertEqual(ada.position, "CTO")
        self.assertEqual(ada.notes, "keep me")
        self.assertEqual(ada.attendance, self.ada.attendance)
        self.assertEqual(ada.email, "ada@acme.test")
        # The workbook agrees with what the invoice already says, so nothing
        # moves there either.
        self.assertEqual(
            BookEvent.objects.get(pk=self.invoice.pk).paid_or_free, "Paid"
        )
        # Bob is named by the workbook, and his values already agree, so every
        # column of his is unchanged too.
        self.assert_untouched(self.bob)
        # And the delegate on the other invoice is never in scope at all.
        self.assert_untouched(self.control)

    def test_updated_at_is_left_alone_by_default(self):
        before = BookDelegate.objects.get(pk=self.ada.pk).updated_at
        self.run_command([("INV-UDN-1", "Ada Lovelace", 0, "Free")], "--apply")
        self.assertEqual(
            BookDelegate.objects.get(pk=self.ada.pk).updated_at, before
        )

    def test_touch_updated_at_stamps(self):
        before = BookDelegate.objects.get(pk=self.ada.pk).updated_at
        self.run_command(
            [("INV-UDN-1", "Ada Lovelace", 0, "Free")],
            "--apply", "--touch-updated-at",
        )
        self.assertGreater(
            BookDelegate.objects.get(pk=self.ada.pk).updated_at, before
        )

    def test_fields_flag_writes_one_column_only(self):
        self.run_command(
            [("INV-UDN-1", "Ada Lovelace", 0, "Free")],
            "--apply", "--fields", "delegate-number",
        )
        ada = BookDelegate.objects.get(pk=self.ada.pk)
        self.assertEqual(ada.delegate_number, 0)
        self.assertIsNone(ada.delegate_paid_or_free)


class PaidFreeSyncTests(UpdateDelegateNumberPaidFreeBase):
    """
    The default target, and the reason this command exists in this shape.

    Payable / Free is resolved as `delegate_paid_or_free or
    invoice.paid_or_free`, so the pair of columns is the value and either one
    alone is half an answer. These pin the rule the CRM's own booking modal
    follows, stated in frontend/src/api/bookings.js; where the delegates agree
    the invoice carries the value and the overrides are cleared, and an override
    survives only to carry a real per-delegate difference.
    """

    def test_value_inherited_from_the_invoice_counts_as_correct(self):
        # The invoice says Paid and the override is NULL, so the table already
        # shows Paid. There is nothing to correct on either column.
        out = self.run_command([
            ("INV-UDN-1", "Ada Lovelace", 1, "Paid"),
            ("INV-UDN-1", "Bob Stone", 1, "Paid"),
        ])
        self.assertIn("Payable / Free already correct  : 2", out)
        self.assertIn("invoice paid_or_free to change  : 0", out)
        self.assertIn("delegate override to change     : 0", out)

    def test_agreement_moves_the_invoice_and_leaves_no_override(self):
        self.run_command(
            [
                ("INV-UDN-1", "Ada Lovelace", 1, "Free"),
                ("INV-UDN-1", "Bob Stone", 1, "Free"),
            ],
            "--apply",
        )
        # The shared fact goes on the shared row, so anything reading
        # invoice.paid_or_free agrees with what the table displays.
        self.assertEqual(
            BookEvent.objects.get(pk=self.invoice.pk).paid_or_free, "Free"
        )
        self.assertIsNone(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_paid_or_free
        )
        self.assertIsNone(
            BookDelegate.objects.get(pk=self.bob.pk).delegate_paid_or_free
        )

    def test_a_redundant_override_is_cleared_rather_than_rewritten(self):
        # Ada already carries an override saying what the invoice will now say.
        self.ada.delegate_paid_or_free = "Free"
        self.ada.save()
        self.run_command(
            [
                ("INV-UDN-1", "Ada Lovelace", 1, "Free"),
                ("INV-UDN-1", "Bob Stone", 1, "Free"),
            ],
            "--apply",
        )
        self.assertEqual(
            BookEvent.objects.get(pk=self.invoice.pk).paid_or_free, "Free"
        )
        self.assertIsNone(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_paid_or_free
        )

    def test_disagreement_puts_the_majority_on_the_invoice(self):
        cat = BookDelegate.objects.create(
            invoice=self.invoice, event_code=EVENT_CODE, edition=2026,
            first_name="Dee", last_name="Ray", email="dee@acme.test",
        )
        self.run_command(
            [
                ("INV-UDN-1", "Ada Lovelace", 1, "Free"),
                ("INV-UDN-1", "Bob Stone", 1, "Free"),
                ("INV-UDN-1", "Dee Ray", 1, "Paid"),
            ],
            "--apply",
        )
        self.assertEqual(
            BookEvent.objects.get(pk=self.invoice.pk).paid_or_free, "Free"
        )
        self.assertIsNone(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_paid_or_free
        )
        # Only the one genuine difference is carried as an override.
        self.assertEqual(
            BookDelegate.objects.get(pk=cat.pk).delegate_paid_or_free, "Paid"
        )

    def test_a_tie_keeps_the_value_the_invoice_already_holds(self):
        self.run_command(
            [
                ("INV-UDN-1", "Ada Lovelace", 1, "Free"),
                ("INV-UDN-1", "Bob Stone", 1, "Paid"),
            ],
            "--apply",
        )
        # One each, so a coin flip would rewrite the invoice for nothing.
        self.assertEqual(
            BookEvent.objects.get(pk=self.invoice.pk).paid_or_free, "Paid"
        )
        self.assertEqual(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_paid_or_free,
            "Free",
        )
        self.assertIsNone(
            BookDelegate.objects.get(pk=self.bob.pk).delegate_paid_or_free
        )

    def test_partial_coverage_leaves_the_invoice_alone(self):
        # Bob is on this invoice and not in the workbook. Moving the invoice to
        # Free would silently re-label him, so it is not moved.
        out = self.run_command(
            [("INV-UDN-1", "Ada Lovelace", 1, "Free")], "--apply",
        )
        self.assertIn("workbook covers only some of", out)
        self.assertEqual(
            BookEvent.objects.get(pk=self.invoice.pk).paid_or_free, "Paid"
        )
        self.assertEqual(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_paid_or_free,
            "Free",
        )
        # Bob is untouched, and still displays what he displayed before.
        self.assert_untouched(self.bob)

    def test_unreadable_value_is_reported_and_skipped(self):
        out = self.run_command(
            [("INV-UDN-1", "Ada Lovelace", 1, "Maybe")], "--apply",
        )
        self.assertIn("Unreadable Paid/Free cell", out)
        self.assert_untouched(self.ada)


class PaidFreeOtherTargetTests(UpdateDelegateNumberPaidFreeBase):

    def test_delegate_target_writes_the_override_only(self):
        self.run_command(
            [
                ("INV-UDN-1", "Ada Lovelace", 1, "Free"),
                ("INV-UDN-1", "Bob Stone", 1, "Free"),
            ],
            "--apply", "--paid-free-target", "delegate",
        )
        self.assertEqual(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_paid_or_free,
            "Free",
        )
        # Documented consequence of this target, and the reason it is not the
        # default; the invoice column keeps the old value.
        self.assertEqual(
            BookEvent.objects.get(pk=self.invoice.pk).paid_or_free, "Paid"
        )

    def test_force_delegate_override_writes_the_inherited_value_down(self):
        self.run_command(
            [("INV-UDN-1", "Ada Lovelace", 1, "Paid")],
            "--apply", "--paid-free-target", "delegate",
            "--force-delegate-override",
        )
        self.assertEqual(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_paid_or_free,
            "Paid",
        )

    def test_invoice_target_reports_the_override_that_would_win(self):
        self.ada.delegate_paid_or_free = "Paid"
        self.ada.save()
        out = self.run_command(
            [
                ("INV-UDN-1", "Ada Lovelace", 1, "Free"),
                ("INV-UDN-1", "Bob Stone", 1, "Free"),
            ],
            "--apply", "--paid-free-target", "invoice",
        )
        self.assertEqual(
            BookEvent.objects.get(pk=self.invoice.pk).paid_or_free, "Free"
        )
        # The invoice moved, and Ada still displays Paid, so the run is told to
        # say so rather than reporting a clean success.
        self.assertIn("displaying the OLD value", out)
        self.assertEqual(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_paid_or_free,
            "Paid",
        )


class BlankCellTests(UpdateDelegateNumberPaidFreeBase):

    def test_blank_cells_are_skipped(self):
        self.ada.delegate_paid_or_free = "Free"
        self.ada.save()
        out = self.run_command([("INV-UDN-1", "Ada Lovelace", "", "")], "--apply")
        self.assertIn("Blank cells skipped", out)
        ada = BookDelegate.objects.get(pk=self.ada.pk)
        self.assertEqual(ada.delegate_number, 1)
        self.assertEqual(ada.delegate_paid_or_free, "Free")

    def test_allow_clear_erases_the_override(self):
        self.ada.delegate_paid_or_free = "Free"
        self.ada.save()
        self.run_command(
            [("INV-UDN-1", "Ada Lovelace", 1, "")], "--apply", "--allow-clear",
        )
        self.assertIsNone(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_paid_or_free
        )


class MatchingTests(UpdateDelegateNumberPaidFreeBase):

    def test_invoice_case_and_spacing_are_tolerated(self):
        self.run_command([(" inv-udn-1 ", "Ada Lovelace", 0, "Free")], "--apply")
        self.assertEqual(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_number, 0
        )

    def test_middle_initial_punctuation_is_tolerated(self):
        self.ada.first_name = "Ada"
        self.ada.last_name = "G. Lovelace"
        self.ada.save()
        self.run_command([("INV-UDN-1", "Ada G Lovelace", 0, "Free")], "--apply")
        self.assertEqual(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_number, 0
        )

    def test_unmatched_row_is_reported_and_not_created(self):
        out = self.run_command(
            [("INV-UDN-1", "Nobody Here", 0, "Free")], "--apply",
        )
        self.assertIn("Unmatched, 1 row(s)", out)
        self.assertIn("the invoice is in the CRM, this delegate is not", out)
        self.assertEqual(BookDelegate.objects.count(), 3)

    def test_unknown_invoice_says_so(self):
        out = self.run_command([("INV-NOPE", "Ada Lovelace", 0, "Free")])
        self.assertIn("the invoice is not in the CRM at all", out)

    def test_a_name_on_two_stored_rows_is_left_alone(self):
        twin = BookDelegate.objects.create(
            invoice=self.invoice, event_code=EVENT_CODE, edition=2026,
            first_name="Ada", last_name="Lovelace", email="ada2@acme.test",
            delegate_number=1,
        )
        out = self.run_command([("INV-UDN-1", "Ada Lovelace", 0, "Free")], "--apply")
        self.assertIn("Ambiguous, 1 row(s), left untouched", out)
        self.assert_untouched(self.ada)
        self.assert_untouched(twin)

    def test_two_workbook_rows_pair_onto_two_stored_rows_in_order(self):
        twin = BookDelegate.objects.create(
            invoice=self.invoice, event_code=EVENT_CODE, edition=2026,
            first_name="Ada", last_name="Lovelace", email="ada2@acme.test",
            delegate_number=1,
        )
        self.run_command(
            [
                ("INV-UDN-1", "Ada Lovelace", 0, "Free"),
                ("INV-UDN-1", "Ada Lovelace", 1, "Paid"),
            ],
            "--apply",
        )
        self.assertEqual(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_number, 0
        )
        self.assertEqual(
            BookDelegate.objects.get(pk=twin.pk).delegate_number, 1
        )

    def test_email_fallback_rescues_a_renamed_row(self):
        out = self.run_command(
            [("INV-UDN-1", "Ada Byron", 0, "Free", "ada@acme.test")],
            "--apply", "--fallback-email",
        )
        self.assertIn("Matched on invoice plus email: 1", out)
        self.assertEqual(
            BookDelegate.objects.get(pk=self.ada.pk).delegate_number, 0
        )

    def test_email_match_is_named_in_the_report_without_the_flag(self):
        out = self.run_command(
            [("INV-UDN-1", "Ada Byron", 0, "Free", "ada@acme.test")],
        )
        self.assertIn("the email matches, the name differs", out)
