"""
book_event/tests_booking_code_canonical.py
──────────────────────────────────────────
Pins the three things that make the booking_code spelling fix safe:

  1. canonicalize() rewrites ONLY whole-string key matches, and returns anything
     else byte-for-byte. This is the guard against a "fix" that silently edits
     free text it does not understand.
  2. The write chokepoints — BookEvent.save(), BookDelegate.save() and the
     webhook literal — all produce "Delegate", so no new lowercase row can be
     created by any path.
  3. repair() is exact-match and idempotent, so the migration can run on any
     database, twice, without touching a row it should not.

The frontend list is read from disk rather than restated here: if
constants.js gains a code and the backend list does not, that is precisely the
drift this asserts against, and restating the list in the test would hide it.
"""
import re
from datetime import date
from pathlib import Path

from django.test import TestCase

from book_delegate.models import BookDelegate
from book_event.booking_code_canonical import (
    DEFAULT_BOOKING_CODE, canonical_codes, canonicalize, canonicalize_on_save,
    comparison_key, is_canonical,
)
from book_event.booking_code_repair import plan, repair
from book_event.models import BookEvent

FRONTEND_CONSTANTS = (Path(__file__).resolve().parents[2]
                      / "frontend" / "src" / "lib" / "constants.js")


class CanonicalizeTests(TestCase):

    def test_the_reported_case(self):
        self.assertEqual(canonicalize("delegate"), "Delegate")

    def test_case_and_spacing_variants_all_land_on_the_canonical_spelling(self):
        for raw in ("delegate", "DELEGATE", "  Delegate  ", "dElEgAtE"):
            self.assertEqual(canonicalize(raw), "Delegate", raw)
        for raw in ("speaker / slv spex", "Speaker/SLV SpEx", "Speaker /  SLV  SpEx"):
            self.assertEqual(canonicalize(raw), "Speaker / SLV SpEx", raw)
        self.assertEqual(canonicalize("add-ons"), "Add-Ons")
        self.assertEqual(canonicalize("spp / group pass"), "SPP / Group Pass")

    def test_an_unknown_code_is_returned_untouched(self):
        # Including its odd spacing and casing: this function is not a cleaner.
        for raw in ("  Corporate  bundle ", "xyz", "Speaker Table 2", "SPPX", "Deleg"):
            self.assertEqual(canonicalize(raw), raw, raw)

    def test_empty_values_pass_through_unchanged(self):
        self.assertEqual(canonicalize(""), "")
        self.assertIsNone(canonicalize(None))

    def test_separators_are_never_dropped_from_the_key(self):
        # "Add-Ons" and "AddOns" must NOT collide — the over-match this codebase
        # has been burned by before.
        self.assertNotEqual(comparison_key("Add-Ons"), comparison_key("AddOns"))
        self.assertEqual(canonicalize("AddOns"), "AddOns")

    def test_every_canonical_code_is_a_fixed_point(self):
        for code in canonical_codes():
            self.assertTrue(is_canonical(code), code)

    def test_the_default_webhook_code_is_itself_canonical(self):
        self.assertEqual(canonicalize(DEFAULT_BOOKING_CODE), DEFAULT_BOOKING_CODE)
        self.assertEqual(DEFAULT_BOOKING_CODE, "Delegate")

    def test_the_backend_list_matches_the_frontend_dropdown(self):
        source = FRONTEND_CONSTANTS.read_text(encoding="utf-8")
        block = re.search(r"export const BOOKING_CODES = \[(.*?)\];", source, re.S)
        self.assertIsNotNone(block, "BOOKING_CODES not found in constants.js")
        frontend = re.findall(r"'([^']*)'", block.group(1))
        self.assertEqual(sorted(frontend), sorted(canonical_codes()))


class SaveChokepointTests(TestCase):
    """No write path can store a non-canonical spelling of a known code."""

    def _invoice(self, code):
        return BookEvent.objects.create(invoice_number=f"INV-{code}",
                                        event_code="AFS - JS", booking_code=code)

    def test_bookevent_save_canonicalises(self):
        self.assertEqual(self._invoice("delegate").booking_code, "Delegate")

    def test_bookevent_save_leaves_unknown_codes_alone(self):
        self.assertEqual(self._invoice("bespoke thing").booking_code, "bespoke thing")

    def test_bookdelegate_save_canonicalises_its_own_code(self):
        invoice = self._invoice("Delegate")
        d = BookDelegate.objects.create(invoice=invoice, first_name="A", last_name="B",
                                        email="a@b.com", booking_code="speaker")
        self.assertEqual(d.booking_code, "Speaker")

    def test_a_code_inherited_from_the_invoice_is_canonical_too(self):
        # The invoice is forced non-canonical behind save()'s back, so the
        # delegate genuinely inherits a lowercase value.
        invoice = self._invoice("Delegate")
        BookEvent.objects.filter(pk=invoice.pk).update(booking_code="delegate")
        invoice.refresh_from_db()
        d = BookDelegate.objects.create(invoice=invoice, first_name="A", last_name="B",
                                        email="c@b.com")
        self.assertEqual(d.booking_code, "Delegate")


class RepairTests(TestCase):

    def setUp(self):
        for i, code in enumerate(("delegate", "delegate", "DELEGATE", "Speaker",
                                  "house account")):
            inv = BookEvent.objects.create(invoice_number=f"INV-{i}",
                                           event_code="AFS - JS")
            BookEvent.objects.filter(pk=inv.pk).update(booking_code=code)

    def _stored(self):
        return sorted(BookEvent.objects.values_list("booking_code", flat=True))

    def test_plan_reports_only_the_non_canonical_spellings(self):
        self.assertEqual({(s, t): n for s, t, n in plan(BookEvent)},
                         {("delegate", "Delegate"): 2, ("DELEGATE", "Delegate"): 1})

    def test_plan_alone_writes_nothing(self):
        plan(BookEvent)
        self.assertIn("delegate", self._stored())

    def test_repair_rewrites_the_rows_and_leaves_the_rest(self):
        repair(BookEvent, apply=True)
        self.assertEqual(self._stored(),
                         ["Delegate", "Delegate", "Delegate", "Speaker", "house account"])

    def test_repair_is_idempotent(self):
        repair(BookEvent, apply=True)
        self.assertEqual(repair(BookEvent, apply=True), [])

    def test_repair_is_a_noop_on_an_already_canonical_database(self):
        BookEvent.objects.all().delete()
        BookEvent.objects.create(invoice_number="INV-C", event_code="AFS - JS",
                                 booking_code="Delegate")
        self.assertEqual(repair(BookEvent, apply=True), [])


class RestrictedSaveTests(TestCase):
    """
    The hole that made a stale row stay stale.

    The webhook updates an existing booking with save(update_fields=[...]),
    listing only the fields the payload actually changed. booking_code is not
    one of them for a row that already has a code, so correcting the attribute
    without widening update_fields would compute the fix and then throw it away.
    """

    def setUp(self):
        self.invoice = BookEvent.objects.create(
            invoice_number="INV-STALE", event_code="AFS - JS", booking_code="Delegate")
        # Behind save()'s back, so the row is genuinely stale on disk.
        BookEvent.objects.filter(pk=self.invoice.pk).update(booking_code="delegate")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.booking_code, "delegate")

    def test_a_restricted_save_of_an_unrelated_field_still_repairs_the_code(self):
        self.invoice.company_name = "Acme"
        self.invoice.save(update_fields=["company_name"])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.booking_code, "Delegate")
        self.assertEqual(self.invoice.company_name, "Acme")

    def test_a_delegate_restricted_save_repairs_its_code_too(self):
        d = BookDelegate.objects.create(invoice=self.invoice, first_name="A",
                                        last_name="B", email="s@b.com",
                                        booking_code="Delegate")
        BookDelegate.objects.filter(pk=d.pk).update(booking_code="delegate")
        d.refresh_from_db()
        d.position = "CTO"
        d.save(update_fields=["position"])
        d.refresh_from_db()
        self.assertEqual(d.booking_code, "Delegate")

    def test_update_fields_is_widened_not_replaced(self):
        _, kwargs = canonicalize_on_save(self.invoice, (), {"update_fields": ["company_name"]})
        self.assertEqual(kwargs["update_fields"], ["company_name", "booking_code"])

    def test_an_already_canonical_row_does_not_widen_update_fields(self):
        canonical = BookEvent(invoice_number="INV-OK", booking_code="Delegate")
        _, kwargs = canonicalize_on_save(canonical, (), {"update_fields": ["company_name"]})
        self.assertEqual(kwargs["update_fields"], ["company_name"])

    def test_an_unrestricted_save_is_left_alone(self):
        _, kwargs = canonicalize_on_save(self.invoice, (), {})
        self.assertNotIn("update_fields", kwargs)
        self.assertEqual(self.invoice.booking_code, "Delegate")


class WebhookIntakeTests(TestCase):
    """
    End to end through the REAL processor, which is where this started.

    Driven as WebhookProcessor(log).process(), the same way webhooks/views.py
    drives it, so the assertion covers the actual production path rather than a
    reconstruction of it.
    """

    PAYLOAD = {
        "InvoiceNumber": "WH-001",
        "Eventcode": "AFS - JS",
        "Eventname": "AFS JS",
        "DelegateCompanyName": "Acme",
        "Delegates": [{"Email": "wh@acme.com", "FirstName": "W", "LastName": "H"}],
    }

    def _ingest(self, payload):
        from events.models import Event
        from webhooks.models import WebhookLog
        from webhooks.services import WebhookProcessor

        Event.objects.get_or_create(
            event_code="AFS - JS",
            defaults={"name": "AFS JS", "event_date": date(2026, 9, 1),
                      "web_bookings": True})
        log = WebhookLog.objects.create(payload=payload)
        ok, _ = WebhookProcessor(log).process()
        log.refresh_from_db()
        self.assertTrue(ok, log.error_message or log.processing_notes)
        return log

    def test_a_booking_created_by_the_webhook_stores_the_canonical_spelling(self):
        self._ingest(dict(self.PAYLOAD))
        invoice = BookEvent.objects.get(invoice_number="WH-001")
        self.assertEqual(invoice.booking_code, "Delegate")
        delegates = BookDelegate.objects.filter(invoice=invoice)
        self.assertTrue(delegates.exists())
        for d in delegates:
            self.assertEqual(d.booking_code, "Delegate")

    def test_a_second_webhook_touch_repairs_a_row_left_lowercase_by_the_old_code(self):
        # Exactly the production situation: the row was written by the previous
        # version of services.py, and a later webhook updates something else.
        self._ingest(dict(self.PAYLOAD))
        invoice = BookEvent.objects.get(invoice_number="WH-001")
        BookEvent.objects.filter(pk=invoice.pk).update(booking_code="delegate")
        BookDelegate.objects.filter(invoice=invoice).update(booking_code="delegate")

        self._ingest({**self.PAYLOAD, "DelegateCompanyName": "Acme Holdings"})

        invoice.refresh_from_db()
        self.assertEqual(invoice.booking_code, "Delegate")
        self.assertEqual(invoice.company_name, "Acme Holdings")
