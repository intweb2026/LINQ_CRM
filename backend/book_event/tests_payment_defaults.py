"""
book_event/tests_payment_defaults.py
────────────────────────────────────
A new booking is Pending, and its payment TYPE is blank.

Payment status is never empty in this CRM: a booking nobody has paid for is
waiting to be paid, which is what Pending says. Payment type is the opposite —
Stripe or Bank is not known until money moves, and defaulting it to Stripe
asserted a method nobody had chosen. The two are asserted together here so the
pair cannot drift apart again.

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


class PaymentDefaultTests(TestCase):

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

    def test_model_defaults_are_pending_and_blank_type(self):
        invoice = BookEvent.objects.create(
            invoice_number="INV-BLANK-1", event_code="AIU - AD",
            company_name="Acme", currency="USD",
        )
        self.assertEqual(invoice.payment_status, "Pending")
        self.assertEqual(invoice.payment_type, "")

    def test_defaults_pass_choice_validation(self):
        # full_clean() is run by the mass-update engine and the choice-typed
        # filters: Pending must clear it, and so must a blank payment TYPE.
        invoice = BookEvent(
            invoice_number="INV-BLANK-2", event_code="AIU - AD",
            company_name="Acme", currency="USD",
        )
        invoice.full_clean(exclude=["sales_executive"])

    def test_web_booking_stating_no_status_is_pending(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 201, resp.content)
        invoice = BookEvent.objects.get(invoice_number="AIU26CAL-3100")
        self.assertEqual(invoice.payment_status, "Pending")
        self.assertEqual(invoice.payment_type, "")

    def test_a_stated_status_is_still_stored(self):
        resp = self.post(PaymentStatus="Paid", PaymentType="Stripe")
        self.assertEqual(resp.status_code, 201, resp.content)
        invoice = BookEvent.objects.get(invoice_number="AIU26CAL-3100")
        self.assertEqual(invoice.payment_status, "Paid")
        self.assertEqual(invoice.payment_type, "Stripe")
