"""
book_delegate/tests_delegate_number_backfill.py
────────────────────────────────────────────────
`manage.py backfill_delegate_numbers` — the Delegate Number correction sheet.

WHAT THIS PINS, AND WHY EACH ONE MATTERS
1.  A dry run writes NOTHING. That is the default mode, so if it ever wrote, a
    person exploring what the sheet would do would have already done it.
2.  Only the rows the sheet identifies are written, and on those rows only
    delegate_number moves. This is the whole point of the command; a control
    delegate that the sheet never mentions is asserted field by field, because
    "the numbers look right" would not have caught a save() rewriting
    event_code, booking_code or the invoice's accounts contact email.
3.  A row whose values disagree with the stored record is SKIPPED, not written.
    Three flavours are pinned separately because they fail differently; a
    mismatched email, a mismatched name, and a sheet whose stated current
    number is not the stored one. The last is the sheet-built-on-stale-data
    case, which is invisible to every other check.
4.  Matching survives the spelling differences a hand-built sheet really has;
    invoice case and spacing, name order, an event code carrying its edition.
    Without this the command would report NOT FOUND for rows that are perfectly
    fine and a person would go and "fix" the database to suit it.
5.  updated_at is NOT stamped by default, and IS with --touch-updated-at.
    BookDelegateViewSet.ordering is ["-updated_at", "-id"], so the default
    matters; a backfill that stamped would bury every real edit under itself.
6.  The .xlsx path works, not just the CSV path. The file people are handed
    this for is a workbook.

    python manage.py test book_delegate.tests_delegate_number_backfill
"""
import csv
from io import StringIO
from pathlib import Path
from tempfile import mkdtemp

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from book_delegate.models import BookDelegate
from book_event.models import BookEvent

EVENT_CODE = "DNB - AA"


class BackfillDelegateNumbersBase(TestCase):
    """
    Two invoices. INV-DNB-1 carries three delegates, INV-DNB-2 carries the
    control delegate that no sheet in this file ever mentions.
    """

    def setUp(self):
        self.tmp = Path(mkdtemp(prefix="dnb-"))
        self.report = self.tmp / "report.md"

        self.invoice = BookEvent.objects.create(
            invoice_number="INV-DNB-1", event_code=EVENT_CODE, edition=2026,
            payment_status="Paid", booking_code="Speaker", company_name="Acme",
            currency="USD",
        )
        self.other_invoice = BookEvent.objects.create(
            invoice_number="INV-DNB-2", event_code=EVENT_CODE, edition=2026,
            payment_status="Paid", booking_code="Speaker", company_name="Globex",
            currency="USD",
        )
        self.ada = BookDelegate.objects.create(
            invoice=self.invoice, event_code=EVENT_CODE, edition=2026,
            first_name="Ada", last_name="Lovelace", email="ada@acme.test",
            delegate_number=1,
        )
        self.bob = BookDelegate.objects.create(
            invoice=self.invoice, event_code=EVENT_CODE, edition=2026,
            first_name="Bob", last_name="Stone", email="bob@acme.test",
            delegate_number=2,
        )
        self.cat = BookDelegate.objects.create(
            invoice=self.invoice, event_code=EVENT_CODE, edition=2026,
            first_name="Cat", last_name="Moss", email="cat@acme.test",
            delegate_number=3,
        )
        # The control. Deliberately on its own invoice, deliberately holding the
        # same delegate_number as one of the rows above, so a write that leaked
        # across invoices would show up as a changed number here.
        self.zoe = BookDelegate.objects.create(
            invoice=self.other_invoice, event_code=EVENT_CODE, edition=2026,
            first_name="Zoe", last_name="Quinn", email="zoe@globex.test",
            delegate_number=1,
        )
        self.control_before = self._snapshot(self.zoe.pk)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _snapshot(self, pk):
        return (BookDelegate.objects.filter(pk=pk)
                .values("delegate_number", "event_code", "edition",
                        "booking_code", "first_name", "last_name", "email",
                        "attendance", "delegate_count", "updated_at",
                        "created_at", "booked_on")
                .first())

    def _sheet(self, rows, headers=None, name="sheet.csv"):
        """A CSV at a real path. Headers default to the keys of the first row."""
        headers = headers or list(rows[0].keys())
        path = self.tmp / name
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def _run(self, path, *flags):
        out = StringIO()
        call_command("backfill_delegate_numbers", str(path), *flags,
                     f"--report={self.report}", stdout=out, stderr=out)
        return out.getvalue()

    def _numbers(self):
        return dict(BookDelegate.objects.values_list("pk", "delegate_number"))

    def assertControlUntouched(self):
        self.assertEqual(self._snapshot(self.zoe.pk), self.control_before)


class DryRunTests(BackfillDelegateNumbersBase):

    def test_dry_run_writes_nothing(self):
        before = self._numbers()
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "7"},
        ])
        output = self._run(path)

        self.assertEqual(self._numbers(), before)
        self.assertIn("Dry run, nothing written", output)
        self.assertIn("1 row(s) would change", output)
        self.assertControlUntouched()

    def test_report_is_written_on_a_dry_run(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "7"},
        ])
        self._run(path)
        text = self.report.read_text(encoding="utf-8")
        self.assertIn("dry run, nothing written", text)
        self.assertIn("## WRITE, 1 row(s)", text)
        self.assertIn("ada@acme.test", text)


class ScopeOfTheWriteTests(BackfillDelegateNumbersBase):

    def test_only_the_listed_rows_change_and_only_that_column(self):
        ada_before = self._snapshot(self.ada.pk)
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "9"},
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "bob@acme.test",
             "New Delegate Number": "8"},
        ])
        output = self._run(path, "--apply")

        self.assertIn("Updated delegate_number on 2 delegate(s)", output)
        self.ada.refresh_from_db()
        self.bob.refresh_from_db()
        self.cat.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 9)
        self.assertEqual(self.bob.delegate_number, 8)
        # Never mentioned by the sheet, on the SAME invoice as the two that were.
        self.assertEqual(self.cat.delegate_number, 3)
        self.assertControlUntouched()

        after = self._snapshot(self.ada.pk)
        changed = {k for k in after if after[k] != ada_before[k]}
        self.assertEqual(changed, {"delegate_number"})

    def test_save_side_effects_do_not_run(self):
        """
        The write is a queryset .update(), so BookDelegate.save() never fires.

        Pinned through the one side effect that is visible from outside the row;
        save() copies a delegate's email into a blank invoice accounts contact.
        It has to be blanked first, because creating the delegates in setUp
        went through save() and therefore already filled it, which is exactly
        the behaviour being pinned as ABSENT here. Blanked with a queryset
        .update() rather than by saving the invoice, so nothing else moves.
        A number correction has no business deciding who gets billed.
        """
        BookEvent.objects.filter(pk=self.invoice.pk).update(accounts_contact_email="")
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "4"},
        ])
        self._run(path, "--apply")

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.accounts_contact_email, "")

    def test_unchanged_rows_are_not_rewritten(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "1"},
        ])
        output = self._run(path, "--apply")
        self.assertIn("Updated delegate_number on 0 delegate(s)", output)
        self.assertIn("UNCHANGED", output)

    def test_blank_number_leaves_the_row_alone(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": ""},
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "bob@acme.test",
             "New Delegate Number": "5"},
        ])
        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 1)
        self.assertEqual(self.bob.delegate_number, 5)


class VerificationTests(BackfillDelegateNumbersBase):

    def test_email_that_is_not_on_the_invoice_is_not_written(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Name": "Ada Lovelace",
             "Delegate Email": "wrong@acme.test", "New Delegate Number": "9"},
        ])
        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 1)
        self.assertIn("DELEGATE_NOT_FOUND",
                      self.report.read_text(encoding="utf-8"))

    def test_name_disagreeing_with_the_matched_row_is_a_conflict(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "Name": "Someone Else", "New Delegate Number": "9"},
        ])
        output = self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 1)
        self.assertIn("CONFLICT", output)
        self.assertIn("still unfixed", output)

    def test_stale_current_number_in_the_sheet_is_a_conflict(self):
        """
        The sheet-built-against-the-wrong-data case.

        Two number columns; the sheet claims Ada is currently 3 when she is 1.
        Nothing else in the row is wrong, so this is the only check that can
        catch it, and it must catch it, because a sheet built off a stale export
        has every other row silently misaligned too.
        """
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "Delegate Number": "3", "New Delegate Number": "9"},
        ])
        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 1)
        text = self.report.read_text(encoding="utf-8")
        self.assertIn("## CONFLICT", text)
        self.assertIn("sheet says the current number is 3", text)

    def test_agreeing_current_number_lets_the_row_through(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "Delegate Number": "1", "New Delegate Number": "9"},
        ])
        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 9)

    def test_ignore_conflicts_writes_but_still_reports(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "Name": "Someone Else", "New Delegate Number": "9"},
        ])
        self._run(path, "--apply", "--ignore-conflicts")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 9)
        self.assertIn("forced past", self.report.read_text(encoding="utf-8"))

    def test_unknown_invoice_and_unknown_delegate_report_differently(self):
        path = self._sheet([
            {"Invoice Number": "INV-NOPE", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "9"},
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ghost@acme.test",
             "New Delegate Number": "9"},
        ])
        self._run(path, "--apply")
        self.assertEqual(self._numbers()[self.ada.pk], 1)
        text = self.report.read_text(encoding="utf-8")
        self.assertIn("## INVOICE_NOT_FOUND", text)
        self.assertIn("## DELEGATE_NOT_FOUND", text)

    def test_non_numeric_and_out_of_range_numbers_are_skipped(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "one"},
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "bob@acme.test",
             "New Delegate Number": "0"},
        ])
        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual((self.ada.delegate_number, self.bob.delegate_number), (1, 2))
        self.assertIn("## BAD_NUMBER", self.report.read_text(encoding="utf-8"))

    def test_min_number_zero_allows_a_zero(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "bob@acme.test",
             "New Delegate Number": "0"},
        ])
        self._run(path, "--apply", "--min-number=0")
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.delegate_number, 0)


class MatchingTests(BackfillDelegateNumbersBase):

    def test_invoice_case_and_spacing_still_match(self):
        path = self._sheet([
            {"Invoice Number": "  inv-dnb-1 ", "Delegate Email": "ADA@Acme.test",
             "New Delegate Number": "6"},
        ])
        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 6)

    def test_id_column_is_matched_first(self):
        path = self._sheet([
            {"Id": str(self.cat.pk), "New Delegate Number": "11"},
        ])
        self._run(path, "--apply")
        self.cat.refresh_from_db()
        self.assertEqual(self.cat.delegate_number, 11)
        self.assertControlUntouched()

    def test_name_matching_when_the_sheet_has_no_email(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Name": "Lovelace, Ada",
             "Event Code": "DNB - AA 26", "New Delegate Number": "4"},
        ])
        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 4)

    def test_first_and_last_name_columns_are_used(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "First Name": "Bob",
             "Last Name": "Stone", "New Delegate Number": "4"},
        ])
        self._run(path, "--apply")
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.delegate_number, 4)

    def test_a_mismatched_email_only_falls_back_to_the_name_when_asked(self):
        rows = [{"Invoice Number": "INV-DNB-1", "Delegate Email": "old@acme.test",
                 "Name": "Ada Lovelace", "New Delegate Number": "5"}]
        self._run(self._sheet(rows), "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 1)

        self._run(self._sheet(rows), "--apply", "--match-name",
                  "--ignore-conflicts")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 5)

    def test_two_delegates_with_the_same_name_are_ambiguous(self):
        BookDelegate.objects.create(
            invoice=self.invoice, event_code=EVENT_CODE, edition=2026,
            first_name="Ada", last_name="Lovelace", email="ada2@acme.test",
            delegate_number=4,
        )
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Name": "Ada Lovelace",
             "New Delegate Number": "9"},
        ])
        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 1)
        self.assertIn("## AMBIGUOUS", self.report.read_text(encoding="utf-8"))

    def test_event_code_disagreement_is_a_conflict(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "Event Code": "OTH - ZZ 26", "New Delegate Number": "9"},
        ])
        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 1)
        self.assertIn("event code", self.report.read_text(encoding="utf-8"))


class DuplicateRowTests(BackfillDelegateNumbersBase):

    def test_contradicting_rows_for_one_delegate_write_neither(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "5"},
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "6"},
        ])
        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 1)
        self.assertIn("## DUPLICATE_CLASH",
                      self.report.read_text(encoding="utf-8"))

    def test_repeated_rows_agreeing_write_once(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "5"},
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "5"},
        ])
        output = self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 5)
        self.assertIn("Updated delegate_number on 1 delegate(s)", output)

    def test_two_delegates_left_sharing_a_number_is_a_warning_not_a_block(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "bob@acme.test",
             "New Delegate Number": "1"},
        ])
        output = self._run(path, "--apply")
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.delegate_number, 1)
        self.assertIn("end up used twice", output)
        self.assertIn("duplicate numbers within an invoice",
                      self.report.read_text(encoding="utf-8"))


class UpdatedAtTests(BackfillDelegateNumbersBase):

    def test_updated_at_is_left_alone_by_default(self):
        before = self._snapshot(self.ada.pk)["updated_at"]
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "9"},
        ])
        self._run(path, "--apply")
        self.assertEqual(self._snapshot(self.ada.pk)["updated_at"], before)

    def test_touch_updated_at_stamps_only_the_written_rows(self):
        ada_before = self._snapshot(self.ada.pk)["updated_at"]
        cat_before = self._snapshot(self.cat.pk)["updated_at"]
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "9"},
        ])
        self._run(path, "--apply", "--touch-updated-at")
        self.assertGreater(self._snapshot(self.ada.pk)["updated_at"], ada_before)
        self.assertEqual(self._snapshot(self.cat.pk)["updated_at"], cat_before)
        self.assertControlUntouched()


class SheetShapeTests(BackfillDelegateNumbersBase):

    def test_xlsx_workbook_is_read(self):
        from openpyxl import Workbook

        path = self.tmp / "sheet.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.append(["Invoice Number", "Delegate Email", "New Delegate Number"])
        sheet.append(["INV-DNB-1", "ada@acme.test", 12])
        # A formatted-but-empty trailing row, which real workbooks carry and
        # read_import_rows drops.
        sheet.append([None, None, None])
        book.save(path)

        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 12)

    def test_edition_disagreement_is_a_conflict(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "Edition": "2025", "New Delegate Number": "9"},
        ])
        self._run(path, "--apply")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 1)
        self.assertIn("edition 2025 vs stored 2026",
                      self.report.read_text(encoding="utf-8"))

    def test_informational_columns_do_not_raise_a_warning(self):
        """
        The --export template carries Payment Status, which is neither matched
        on nor verified. It must not report as an unrecognised column, or the
        command's own template would warn about itself.
        """
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "Payment Status": "Paid", "Attendance": "Confirmed",
             "New Delegate Number": "9"},
        ])
        output = self._run(path, "--apply")
        self.assertNotIn("unrecognised column", output)
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 9)

    def test_unrecognised_columns_are_reported_not_silently_dropped(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "9", "Zoho Row Hash": "abc123"},
        ])
        output = self._run(path, "--apply")
        self.assertIn("unrecognised column", output)
        self.assertIn("Zoho Row Hash", output)

    def test_number_column_override_claims_an_unrecognised_header(self):
        """
        The master Google Sheet export heads the delegate number "Delegate
        Count", which is the name of a DIFFERENT field on this model. No alias
        list should guess at that, so the column is named on the command line
        and claimed before every other rule runs.
        """
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "Delegate Count": "0"},
        ])
        self._run(path, "--apply", "--min-number=0",
                  "--number-column=Delegate Count")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 0)
        # The OTHER field of that name is not what was written.
        self.assertEqual(self.ada.delegate_count, 1)
        self.assertControlUntouched()

    def test_number_column_override_naming_a_missing_column_is_refused(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test",
             "New Delegate Number": "9"},
        ])
        with self.assertRaises(CommandError):
            self._run(path, "--number-column=Nope")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.delegate_number, 1)

    def test_a_sheet_with_no_number_column_is_refused(self):
        path = self._sheet([
            {"Invoice Number": "INV-DNB-1", "Delegate Email": "ada@acme.test"},
        ])
        with self.assertRaises(CommandError):
            self._run(path)

    def test_a_sheet_with_no_usable_key_is_refused(self):
        path = self._sheet([{"Event Code": EVENT_CODE, "New Delegate Number": "9"}])
        with self.assertRaises(CommandError):
            self._run(path)

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(CommandError):
            self._run(self.tmp / "does-not-exist.xlsx")


class ExportTests(BackfillDelegateNumbersBase):

    def test_export_round_trips_as_a_no_op(self):
        """
        The template is the safest possible sheet; re-importing it writes nothing.

        Every row carries its Id, its stored number and an empty new number, so
        this exercises the id match, the current-number verification and the
        blank skip in one pass.
        """
        out = StringIO()
        export = self.tmp / "template.csv"
        call_command("backfill_delegate_numbers", f"--export={export}", stdout=out)
        self.assertIn("Wrote 4 row(s)", out.getvalue())

        before = self._numbers()
        self._run(export, "--apply")
        self.assertEqual(self._numbers(), before)
        self.assertControlUntouched()

    def test_export_filtered_by_event_code_ignores_the_edition(self):
        BookEvent.objects.create(
            invoice_number="INV-DNB-3", event_code="OTH - ZZ", edition=2026,
            payment_status="Paid", currency="USD",
        )
        BookDelegate.objects.create(
            invoice_id="INV-DNB-3", event_code="OTH - ZZ", edition=2026,
            first_name="Out", last_name="Scope", email="out@zz.test",
        )
        out = StringIO()
        export = self.tmp / "filtered.csv"
        call_command("backfill_delegate_numbers", f"--export={export}",
                     "--event-code=DNB - AA 26", stdout=out)
        self.assertIn("Wrote 4 row(s)", out.getvalue())

        with export.open(encoding="utf-8-sig", newline="") as handle:
            codes = {row["Event Code"] for row in csv.DictReader(handle)}
        self.assertEqual(codes, {EVENT_CODE})

    def test_export_then_fill_in_writes_exactly_those_rows(self):
        export = self.tmp / "template.csv"
        call_command("backfill_delegate_numbers", f"--export={export}",
                     stdout=StringIO())

        with export.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames
            rows = list(reader)
        for row in rows:
            if row["Delegate Email"] == "cat@acme.test":
                row["New Delegate Number"] = "42"
        filled = self._sheet(rows, headers=headers, name="filled.csv")

        self._run(filled, "--apply")
        for delegate in (self.ada, self.bob, self.cat):
            delegate.refresh_from_db()
        self.assertEqual(self.cat.delegate_number, 42)
        self.assertEqual((self.ada.delegate_number, self.bob.delegate_number), (1, 2))
        self.assertControlUntouched()
