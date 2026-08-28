"""
book_event/tests_blank_payment_defaults.py
──────────────────────────────────────────
A booking nobody has said anything about carries NO payment status.

"Pending" was the model default and the fallback in both intake paths, so every
booking — hand-entered, web form, intake endpoint — asserted a payment state on
the payload's behalf the moment it was created. Blank is the honest value: it
means the question has not been answered, which is different from "answered:
still waiting". Payment Type already defaulted blank and is asserted here beside
it so the pair cannot drift apart again.

The last test is the one that matters most: a payload that DOES state a status
must still store it, because a default that swallowed real values would be the
worse bug of the two.
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from book_event.models import BookEvent
from webhooks.models import WebhookApiKey
from webhooks.tests_event_resolution import make_event


class BlankPaymentDefaultTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        make_event("AIU - AD", web_bookings=True, event_date=date(2026, 8, 27))
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="test-suite", api_key=cls.raw_key)

    def payload(self, **over):
        body = {
            "InvoiceNumber":       "AIU26CAL-3100",
            "Eventcode":           "AIU",
            "Eventname":           "AIU - Summit Agri AI",
            "Date":                "Aug 27, 2026",
            "Currency":            "USD",
            "PreTaxAmount":        "100",
            "TaxAmount":           "0",
            "TotalAmount":         "100",
            "AddOnsTotalAmount":   "0",
            "Discount":            "0",
            "DelegateCompanyName": "Spring Creek Cow Co.",
            "Delegates": [
                {"FirstName": "Brendon", "LastName": "Wheelock",
                 "Email": "brendon@example.test"},
            ],
        }
        body.update(over)
        return body

    def post(self, **over):
        return self.client.post(
            reverse("webhook-ingest"), data=self.payload(**over),
            content_type="application/json", HTTP_X_CRM_API_KEY=self.raw_key,
        )

    def test_model_default_is_blank(self):
        invoice = BookEvent.objects.create(
            invoice_number="INV-BLANK-1", event_code="AIU - AD",
            company_name="Acme", currency="USD",
        )
        self.assertEqual(invoice.payment_status, "")
        self.assertEqual(invoice.payment_type, "")

    def test_blank_passes_choice_validation(self):
        # blank=True is what lets full_clean() accept it — the mass-update engine
        # and the choice-typed filters both run it.
        invoice = BookEvent(
            invoice_number="INV-BLANK-2", event_code="AIU - AD",
            company_name="Acme", currency="USD", payment_status="",
        )
        invoice.full_clean(exclude=["sales_executive"])

    def test_web_booking_stating_no_status_stores_blank(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 201, resp.content)
        invoice = BookEvent.objects.get(invoice_number="AIU26CAL-3100")
        self.assertEqual(invoice.payment_status, "")
        self.assertEqual(invoice.payment_type, "")

    def test_a_stated_status_is_still_stored(self):
        resp = self.post(PaymentStatus="Paid", PaymentType="Stripe")
        self.assertEqual(resp.status_code, 201, resp.content)
        invoice = BookEvent.objects.get(invoice_number="AIU26CAL-3100")
        self.assertEqual(invoice.payment_status, "Paid")
        self.assertEqual(invoice.payment_type, "Stripe")
