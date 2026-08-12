"""
book_event/tests_load_zoho_export.py
─────────────────────────────────────
One test class per requirement of the load command, named so a failure says which
requirement broke.

  1 --dry-run writes nothing        → DryRunTests
  2 whole-load atomicity            → AtomicityTests  (forces a mid-load failure)
  3 one import_batch_id per load    → BatchIdTests
  4 one ActionLog per load          → ActionLogTests
  5 dates via parse_import_date     → DateTests
  6 codes via event_resolver        → EventCodeTests
  7 dependency order                → DependencyOrderTests
  8 idempotency                     → IdempotencyTests
  9 generated invoice numbers       → GeneratedInvoiceTests

The export file itself does not exist yet, so every fixture here is SYNTHETIC and
its column names come from the Zoho Report spellings that
import_bookings_json.py already handles. These tests prove the command's
mechanics; they cannot prove it handles the real file's quirks, and nothing here
should be read as saying otherwise.
"""
import json
import tempfile
import uuid
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from accounts.models import ActionLog
from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event

User = get_user_model()


def make_event(code, name="", date="2026-01-01", web_bookings=True):
    return Event.objects.create(
        event_code=code, official_event_name=name or code,
        event_date=date, web_bookings=web_bookings,
    )


def row(**over):
    """One flat delegate row in Zoho Report spelling."""
    base = {
        "Invoice_Number.Invoice_Number": "INV-1001",
        "Event_Name.Event_Code_with_Year": "BIUK - PM",
        "Invoice_Number.Invoice_Date": "2026-01-15",
        "Sub_Company": "Acme Ltd",
        "Name": "Ada Lovelace",
        "Delegate_Email": "ada@example.com",
        "Direct_Line": "+44 1234",
        "Packages": "Delegate Pass",
        "Status": "Paid",
    }
    base.update(over)
    return base


class _Base(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        make_event("BIUK - PM", "BI UK 2026")
        self.admin = User.objects.create_user(
            username="loader", password="x", role=User.Role.ADMIN,
            email="loader@example.com", first_name="Load", last_name="Er",
        )

    def write(self, rows, name="export.json"):
        path = self.tmp / name
        path.write_text(json.dumps(rows), encoding="utf-8")
        return str(path)

    def load(self, rows, **flags):
        out = StringIO()
        args = [self.write(rows)]
        call_command("load_zoho_export", *args, stdout=out, stderr=out, **flags)
        return out.getvalue()

    def counts(self):
        return (Event.objects.count(), BookEvent.objects.count(),
                BookDelegate.objects.count())


# ══ 1. DRY RUN ══════════════════════════════════════════════════════════════

class DryRunTests(_Base):
    def test_dry_run_writes_absolutely_nothing(self):
        before = self.counts()
        out = self.load([row(), row(**{"Delegate_Email": "b@example.com"})],
                        dry_run=True)
        self.assertEqual(self.counts(), before)
        self.assertIn("DRY RUN", out)

    def test_dry_run_still_reports_what_would_be_created(self):
        out = self.load([row()], dry_run=True)
        self.assertIn("invoices   created", out)
        self.assertIn("delegates  created", out)

    def test_dry_run_writes_no_action_log(self):
        self.load([row()], dry_run=True)
        self.assertEqual(ActionLog.objects.count(), 0)

    def test_dry_run_reports_per_row_rejection_reasons(self):
        out = self.load([row(**{"Invoice_Number.Invoice_Date": "not-a-date"})],
                        dry_run=True)
        self.assertIn("REJECTED ROWS", out)
        self.assertIn("not-a-date", out)


# ══ 2. ATOMICITY ════════════════════════════════════════════════════════════

class AtomicityTests(_Base):
    def test_a_failure_mid_load_leaves_zero_rows(self):
        """
        The whole point. --fail-after aborts partway through the delegate stage,
        by which time events and invoices have already been written. Every one of
        them must be gone.
        """
        rows = [row(**{"Invoice_Number.Invoice_Number": f"INV-{i}",
                       "Delegate_Email": f"d{i}@example.com"})
                for i in range(10)]
        before = self.counts()

        with self.assertRaises(RuntimeError):
            self.load(rows, fail_after=4)

        self.assertEqual(self.counts(), before,
                         "a failure mid-load must roll back every stage")
        self.assertEqual(ActionLog.objects.count(), 0)

    def test_the_same_load_without_the_failure_does_write(self):
        """Control: proves the previous test's zero is the rollback, not a no-op."""
        rows = [row(**{"Invoice_Number.Invoice_Number": f"INV-{i}",
                       "Delegate_Email": f"d{i}@example.com"})
                for i in range(10)]
        self.load(rows)
        self.assertEqual(BookEvent.objects.count(), 10)
        self.assertEqual(BookDelegate.objects.count(), 10)


# ══ 3. BATCH ID ═════════════════════════════════════════════════════════════

class BatchIdTests(_Base):
    def test_every_row_of_one_load_shares_one_batch_id(self):
        self.load([row(**{"Invoice_Number.Invoice_Number": f"INV-{i}",
                          "Delegate_Email": f"d{i}@example.com"})
                   for i in range(5)])
        ids = (set(BookEvent.objects.values_list("import_batch_id", flat=True))
               | set(BookDelegate.objects.values_list("import_batch_id", flat=True)))
        self.assertEqual(len(ids), 1)
        self.assertIsNotNone(ids.pop())

    def test_two_loads_get_different_batch_ids(self):
        self.load([row()])
        self.load([row(**{"Invoice_Number.Invoice_Number": "INV-2",
                          "Delegate_Email": "second@example.com"})])
        ids = set(BookEvent.objects.values_list("import_batch_id", flat=True))
        self.assertEqual(len(ids), 2)

    def test_a_batch_id_can_be_supplied(self):
        given = uuid.uuid4()
        self.load([row()], batch_id=str(given))
        self.assertEqual(BookEvent.objects.first().import_batch_id, given)

    def test_deleting_by_batch_id_removes_exactly_that_load(self):
        """Requirement 3's actual purpose: a reversible load."""
        first = uuid.uuid4()
        self.load([row()], batch_id=str(first))
        self.load([row(**{"Invoice_Number.Invoice_Number": "INV-2",
                          "Delegate_Email": "second@example.com"})])

        BookDelegate.objects.filter(import_batch_id=first).delete()
        BookEvent.objects.filter(import_batch_id=first).delete()

        self.assertEqual(BookEvent.objects.count(), 1)
        self.assertEqual(BookEvent.objects.first().invoice_number, "INV-2")


# ══ 4. ACTION LOG ═══════════════════════════════════════════════════════════

class ActionLogTests(_Base):
    def test_exactly_one_action_log_per_load(self):
        self.load([row(**{"Invoice_Number.Invoice_Number": f"INV-{i}",
                          "Delegate_Email": f"d{i}@example.com"})
                   for i in range(6)])
        self.assertEqual(ActionLog.objects.count(), 1)

    def test_the_action_log_carries_the_batch_id_and_counts(self):
        given = uuid.uuid4()
        self.load([row()], batch_id=str(given))
        log = ActionLog.objects.get()
        self.assertIn(str(given), log.details)
        self.assertIn("delegates created=1", log.details)
        self.assertIn("invoice_numbers_generated=0", log.details)


# ══ 5. DATES ════════════════════════════════════════════════════════════════

class DateTests(_Base):
    def test_an_excel_serial_parses(self):
        # Excel serial 45678 is 2025-01-21 (45292 = 2024-01-01, +386 days).
        self.load([row(**{"Invoice_Number.Invoice_Date": 45678})])
        self.assertEqual(str(BookEvent.objects.get().invoice_date), "2025-01-21")

    def test_a_dirty_hyphenated_string_parses(self):
        self.load([row(**{"Invoice_Number.Invoice_Date": "20 - Dec - 2025"})])
        self.assertEqual(str(BookEvent.objects.get().invoice_date), "2025-12-20")

    def test_dd_mm_yyyy_parses_as_uk(self):
        self.load([row(**{"Invoice_Number.Invoice_Date": "03/04/2026"})])
        self.assertEqual(str(BookEvent.objects.get().invoice_date), "2026-04-03")

    def test_an_unparseable_date_rejects_the_row_quoting_the_raw_value(self):
        """
        The legacy `_parse_date` returned None here, so a whole column of
        unreadable dates was indistinguishable from a column of blanks.
        """
        out = self.load([row(**{"Invoice_Number.Invoice_Date": "13/13/2026"})])
        self.assertEqual(BookEvent.objects.count(), 0)
        self.assertIn("13/13/2026", out)
        self.assertIn("rows rejected             : 1", out)

    def test_the_phantom_serial_60_is_rejected(self):
        out = self.load([row(**{"Invoice_Number.Invoice_Date": 60})])
        self.assertEqual(BookEvent.objects.count(), 0)
        self.assertIn("60", out)

    def test_a_blank_date_is_not_an_error(self):
        self.load([row(**{"Invoice_Number.Invoice_Date": ""})])
        self.assertEqual(BookEvent.objects.count(), 1)
        self.assertIsNone(BookEvent.objects.get().invoice_date)


# ══ 6. EVENT CODES ══════════════════════════════════════════════════════════

class EventCodeTests(_Base):
    def test_an_exact_code_resolves(self):
        self.load([row()])
        self.assertEqual(BookEvent.objects.get().event_code, "BIUK - PM")

    def test_a_boundary_match_resolves(self):
        make_event("BIU/GS - PM", "BIU GS")
        self.load([row(**{"Event_Name.Event_Code_with_Year": "BIU/GS - PM"})])
        self.assertEqual(BookEvent.objects.get().event_code, "BIU/GS - PM")

    def test_a_substring_that_is_not_boundary_anchored_does_not_resolve(self):
        """
        THE bug event_resolver exists for: "BIU" must not attach to "BIUK - PM".
        events/views.py and book_event/views.py never consult the resolver, so
        this is the behaviour the command adds.
        """
        out = self.load([row(**{"Event_Name.Event_Code_with_Year": "BIU"})])
        self.assertEqual(BookEvent.objects.count(), 0)
        self.assertIn("BIU", out)

    def test_an_unknown_code_rejects_the_row(self):
        out = self.load([row(**{"Event_Name.Event_Code_with_Year": "NOPE - ZZ"})])
        self.assertEqual(BookEvent.objects.count(), 0)
        self.assertIn("rows rejected", out)


# ══ 7. DEPENDENCY ORDER ═════════════════════════════════════════════════════

class DependencyOrderTests(_Base):
    def test_an_event_absent_from_the_catalogue_is_created_before_its_invoice(self):
        """
        The order is enforced inside the command: the invoice stage resolves
        against a catalogue the event stage has already written, so a brand-new
        code still lands with its invoice and delegate in ONE run.

        Needs --create-missing-events; see the default-off test below.
        """
        self.load([row(**{"Event_Name.Event_Code_with_Year": "NEWEV - XX",
                          "Event_Name": "Brand New Event"})],
                  create_missing_events=True)
        self.assertTrue(Event.objects.filter(event_code="NEWEV - XX").exists())
        self.assertEqual(BookEvent.objects.count(), 1)
        self.assertEqual(BookDelegate.objects.count(), 1)

    def test_events_are_not_created_by_default(self):
        """
        Guards requirement 6. If this stage created an Event per unseen code, the
        anchored resolver could never reject anything — the invoice stage would
        always find a row just invented from the same string, and a typo would
        become a new event rather than a rejection.
        """
        before = Event.objects.count()
        self.load([row(**{"Event_Name.Event_Code_with_Year": "NEWEV - XX"})])
        self.assertEqual(Event.objects.count(), before)
        self.assertEqual(BookEvent.objects.count(), 0)

    def test_a_delegate_is_linked_to_its_invoice(self):
        self.load([row()])
        delegate = BookDelegate.objects.get()
        self.assertEqual(delegate.invoice_id, "INV-1001")
        self.assertEqual(delegate.invoice.event_code, "BIUK - PM")


# ══ 8. IDEMPOTENCY ══════════════════════════════════════════════════════════

class IdempotencyTests(_Base):
    def test_running_the_same_file_twice_creates_no_duplicates(self):
        rows = [row(**{"Invoice_Number.Invoice_Number": f"INV-{i}",
                       "Delegate_Email": f"d{i}@example.com"})
                for i in range(8)]
        self.load(rows)
        first = self.counts()
        out = self.load(rows)
        self.assertEqual(self.counts(), first)
        self.assertIn("skipped", out)

    def test_the_second_run_reports_them_as_skipped_not_created(self):
        self.load([row()])
        out = self.load([row()])
        self.assertIn("invoices   created      0", out)
        self.assertIn("delegates  created      0", out)

    def test_two_identical_rows_in_one_file_create_one_delegate(self):
        self.load([row(), row()])
        self.assertEqual(BookDelegate.objects.count(), 1)

    def test_a_new_row_added_to_a_rerun_file_is_created(self):
        self.load([row()])
        self.load([row(), row(**{"Invoice_Number.Invoice_Number": "INV-9",
                                 "Delegate_Email": "new@example.com"})])
        self.assertEqual(BookEvent.objects.count(), 2)
        self.assertEqual(BookDelegate.objects.count(), 2)


# ══ 9. GENERATED INVOICE NUMBERS ════════════════════════════════════════════

class GeneratedInvoiceTests(_Base):
    def test_a_missing_invoice_number_is_generated_and_counted(self):
        out = self.load([row(**{"Invoice_Number.Invoice_Number": ""})])
        self.assertEqual(BookEvent.objects.count(), 1)
        self.assertTrue(BookEvent.objects.get().invoice_number.startswith("IMP-"))
        self.assertIn("invoice numbers generated : 1", out)

    def test_generation_is_deterministic_so_a_rerun_does_not_duplicate(self):
        """
        The trap A2 creates for requirement 8. invoices/bulk_import/ uses uuid4
        here, so a second run mints a different number, every idempotency key
        misses, and the load duplicates precisely the rows it should skip.
        """
        rows = [row(**{"Invoice_Number.Invoice_Number": ""})]
        self.load(rows)
        first = BookEvent.objects.get().invoice_number

        self.load(rows)
        self.assertEqual(BookEvent.objects.count(), 1,
                         "a generated invoice number must be reproducible")
        self.assertEqual(BookEvent.objects.get().invoice_number, first)

    def test_rows_with_numbers_are_not_counted_as_generated(self):
        out = self.load([row()])
        self.assertIn("invoice numbers generated : 0", out)
