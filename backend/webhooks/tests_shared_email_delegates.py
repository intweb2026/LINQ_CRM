"""
webhooks/tests_shared_email_delegates.py
─────────────────────────────────────────
Two people on one email address are two delegates.

THE BUG: `_process_delegates` matched an incoming delegate on (invoice, email)
alone, which is not who a delegate is. A booking form sending two owners under
one ranch office address inserted the first person, then found that same row for
the second and UPDATED it, so the first person's name, position and phone number
were overwritten and nobody was left holding them. The delivery answered 201
with delegates_created=1, and the processing notes said "Delegate #2 updated",
so nothing in the logs read as a loss.

The regression payload is the live delivery that surfaced it: invoice
AIU26CAL-2847, Brendon and Emily Wheelock, both on wheelock.ranch@gmail.com.

These tests also hold the properties the fix must not cost:
  * a re-delivery still updates in place, inserting nothing;
  * two payload entries identical in email AND name still collapse to one row,
    since nothing distinguishes them and the unique constraint refuses them;
  * one email on two DIFFERENT invoices was never affected and stays that way.
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from webhooks.models import WebhookApiKey, WebhookLog
from webhooks.tests_event_resolution import make_event

SHARED = "wheelock.ranch@gmail.com"


class SharedEmailDelegateTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        make_event("AIU - AD", web_bookings=True, event_date=date(2026, 8, 27))
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="test-suite", api_key=cls.raw_key)

    # ── Fixtures ──────────────────────────────────────────────────────────────

    def payload(self, delegates=None, invoice="AIU26CAL-2847"):
        """The live delivery, trimmed to the fields these tests read."""
        return {
            "InvoiceNumber":       invoice,
            "Eventcode":           "AIU",
            "Eventname":           "AIU - Summit Agri AI",
            "Date":                "Aug 27, 2026",
            "Packages":            "Regular",
            "Currency":            "USD",
            "PreTaxAmount":        "2790",
            "TaxAmount":           "251.10",
            "TotalAmount":         "3041.1",
            "AddOnsTotalAmount":   "0",
            "Discount":            "0",
            "DelegateCompanyName": "Spring Creek Cow Co.",
            "Delegates": delegates if delegates is not None else [
                {"FirstName": "Brendon", "LastName": "Wheelock", "Email": SHARED,
                 "Position": "Owner", "PhoneNumber": "+16202130441"},
                {"FirstName": "Emily", "LastName": "Wheelock", "Email": SHARED,
                 "Position": "COO", "PhoneNumber": "+13032506590"},
            ],
        }

    def post(self, body=None):
        return self.client.post(
            reverse("webhook-ingest"),
            data=self.payload() if body is None else body,
            content_type="application/json",
            HTTP_X_CRM_API_KEY=self.raw_key,
        )

    # ── The regression ────────────────────────────────────────────────────────

    def test_two_people_one_email_are_two_delegates(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["delegates_created"], 2)

        rows = BookDelegate.objects.filter(invoice_id="AIU26CAL-2847").order_by("id")
        self.assertEqual(
            [(r.first_name, r.position, r.phone_number) for r in rows],
            [("Brendon", "Owner", "+16202130441"),
             ("Emily",   "COO",   "+13032506590")],
        )
        # The real address on BOTH rows. The Excel importer's workaround for the
        # same collision stores a `dup-xxxxxxxx@import.local` placeholder on the
        # second person, which is the other way to lose the data.
        self.assertEqual([r.email for r in rows], [SHARED, SHARED])

    def test_shared_fields_are_shared_and_own_fields_are_not(self):
        self.post()
        rows = BookDelegate.objects.filter(invoice_id="AIU26CAL-2847").order_by("id")
        for row in rows:
            self.assertEqual(row.company_name_raw, "Spring Creek Cow Co.")
            self.assertEqual(row.event_code, "AIU - AD")
            self.assertEqual(row.delegate_ticket_tier, "Regular")
        self.assertNotEqual(rows[0].position, rows[1].position)
        self.assertNotEqual(rows[0].phone_number, rows[1].phone_number)

    def test_invoice_counts_both(self):
        self.post()
        invoice = BookEvent.objects.get(invoice_number="AIU26CAL-2847")
        self.assertEqual(invoice.delegate_count, 2)
        self.assertEqual(invoice.contact_name, "Brendon Wheelock")

    def test_log_reports_two(self):
        resp = self.post()
        log = WebhookLog.objects.get(id=resp.json()["log_id"])
        self.assertEqual(log.status, WebhookLog.Status.SUCCESS)
        self.assertEqual(log.created_delegates_count, 2)
        self.assertEqual(log.records_inserted, 2)
        self.assertEqual(log.records_failed, 0)

    # ── What the fix must not cost ────────────────────────────────────────────

    def test_redelivery_updates_and_inserts_nothing(self):
        self.post()
        resp = self.post()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["delegates_created"], 0)
        self.assertEqual(resp.json()["delegates_skipped"], 2)
        self.assertEqual(BookDelegate.objects.filter(invoice_id="AIU26CAL-2847").count(), 2)

    def test_redelivery_matches_per_person_not_per_position(self):
        """A re-delivery with the two people in the other order still updates."""
        self.post()
        reversed_order = list(reversed(self.payload()["Delegates"]))
        self.post(self.payload(delegates=reversed_order))
        rows = BookDelegate.objects.filter(invoice_id="AIU26CAL-2847").order_by("id")
        self.assertEqual([(r.first_name, r.position) for r in rows],
                         [("Brendon", "Owner"), ("Emily", "COO")])

    def test_case_and_space_differences_are_the_same_person(self):
        self.post()
        self.post(self.payload(delegates=[
            {"FirstName": " brendon ", "LastName": "WHEELOCK",
             "Email": SHARED.upper(), "Position": "Owner"},
        ]))
        self.assertEqual(BookDelegate.objects.filter(invoice_id="AIU26CAL-2847").count(), 2)

    def test_same_email_same_name_twice_collapses_to_one_row(self):
        """
        The TBA case: two entries with nothing at all to tell them apart. There
        is no second person to store, and the (invoice, email, first_name,
        last_name) constraint refuses a second row, so they merge and the notes
        say so — rather than the delivery reporting a failed delegate.
        """
        resp = self.post(self.payload(delegates=[
            {"FirstName": "TBA", "LastName": "", "Email": "tba@turboden.com"},
            {"FirstName": "TBA", "LastName": "", "Email": "tba@turboden.com"},
        ]))
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(BookDelegate.objects.filter(invoice_id="AIU26CAL-2847").count(), 1)
        log = WebhookLog.objects.get(id=resp.json()["log_id"])
        self.assertEqual(log.records_failed, 0)
        self.assertIn("repeats delegate #1", log.processing_notes)

    def test_blank_last_name_is_accepted(self):
        """
        A one-named delegate, which is what a booking form sends when it renders
        an empty surname input. DelegatePayloadSerializer.LastName had no
        allow_blank, so an explicit "" failed validation and took the WHOLE
        delivery down with a 400 — both people lost over a missing surname —
        while omitting the key was fine.
        """
        resp = self.post(self.payload(delegates=[
            {"FirstName": "Brendon", "LastName": "", "Email": SHARED},
        ]))
        self.assertEqual(resp.status_code, 201, resp.content)
        row = BookDelegate.objects.get(invoice_id="AIU26CAL-2847")
        self.assertEqual((row.first_name, row.last_name), ("Brendon", ""))

    def test_blank_email_still_fails_the_delivery(self):
        """
        Not the fix's doing and recorded here so it is not mistaken for it:
        Email is a required EmailField, so a blank one is a payload-level 400
        for the whole delivery. The per-delegate "skipped, no email" branch in
        _process_delegates is reachable only from a retry of a log stored before
        that field was required.
        """
        resp = self.post(self.payload(delegates=[
            {"FirstName": "Nameless", "LastName": "Person", "Email": ""},
            {"FirstName": "Brendon", "LastName": "Wheelock", "Email": SHARED},
        ]))
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(BookDelegate.objects.filter(invoice_id="AIU26CAL-2847").count(), 0)

    def test_one_email_on_two_invoices_is_untouched(self):
        self.post()
        self.post(self.payload(invoice="AIU26CAL-9999"))
        self.assertEqual(BookDelegate.objects.filter(email=SHARED).count(), 4)


class PersonKeyUniquenessTests(TestCase):
    """The model constraint that makes the above storable at all."""

    @classmethod
    def setUpTestData(cls):
        make_event("AIU - AD", web_bookings=True, event_date=date(2026, 8, 27))

    def test_constraint_is_on_the_person_not_the_email(self):
        from django.db import IntegrityError, transaction

        invoice = BookEvent.objects.create(
            invoice_number="AIU26CAL-0001", event_code="AIU - AD",
            invoice_date=date(2026, 8, 27),
        )
        BookDelegate.objects.create(invoice=invoice, event_code="AIU - AD",
                                    first_name="Brendon", last_name="Wheelock",
                                    email=SHARED)
        # Same address, different person: allowed.
        BookDelegate.objects.create(invoice=invoice, event_code="AIU - AD",
                                    first_name="Emily", last_name="Wheelock",
                                    email=SHARED)
        # Same address, same person: still refused.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BookDelegate.objects.create(invoice=invoice, event_code="AIU - AD",
                                            first_name="Brendon", last_name="Wheelock",
                                            email=SHARED)
