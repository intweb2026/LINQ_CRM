"""
webhooks/tests_ingest_logging.py
─────────────────────────────────
Every inbound delivery leaves exactly one WebhookLog row.

THE BUG: a delivery that produced no row is indistinguishable in the logs UI
from a delivery that never arrived — so the failures most worth investigating
were the ones with nothing to show. Two paths did this:

  * a malformed body raised inside the `WebhookLog.objects.create(...)` call
    itself (via `payload=request.data`), so the view's blanket handler turned it
    into a 500 with no row at all;
  * an unexpected exception mid-processing returned 500 and left the row parked
    in `processing` forever.

These tests also hold the ordering fix: `received_at` must be non-null on every
row, because the table sorts on it and Postgres sorts NULLs first under DESC.
"""
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from webhooks.models import WebhookApiKey, WebhookLog
from webhooks.tests_event_resolution import make_event


class IngestAlwaysLogsTests(TestCase):
    """One row per delivery, whatever the outcome."""

    @classmethod
    def setUpTestData(cls):
        make_event("TST - PM", web_bookings=True, event_date=date(2026, 2, 11))
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="test-suite", api_key=cls.raw_key)

    def _payload(self, invoice="INV-LOG-001"):
        return {
            "InvoiceNumber": invoice,
            "Eventcode":     "TST - PM",
            "Eventname":     "whatever",
            "Date":          "2026-02-11",
            "InvoiceDate":   "2026-02-01",
            "Discount":      0,
            "PreTaxAmount":  100,
            "TaxAmount":     0,
            "TotalAmount":   100,
            "AddOnsTotalAmount": 0,
            "Delegates": [{
                "FirstName": "Test",
                "LastName":  "Person",
                "Email":     "test.person@example.com",
            }],
        }

    def _post(self, body=None, *, key=None, content_type="application/json"):
        headers = {"HTTP_X_CRM_API_KEY": key} if key is not None else {}
        return self.client.post(
            reverse("webhook-ingest"),
            data=self._payload() if body is None else body,
            content_type=content_type, **headers,
        )

    # ── The two outcomes the user needs to see ────────────────────────────────

    def test_success_is_logged(self):
        resp = self._post(key=self.raw_key)
        self.assertIn(resp.status_code, (200, 201), resp.content)

        log = WebhookLog.objects.get()
        self.assertEqual(log.status, WebhookLog.Status.SUCCESS)
        self.assertEqual(log.invoice_number, "INV-LOG-001")
        self.assertIsNotNone(log.received_at)

    def test_business_failure_is_logged_with_a_reason(self):
        """A rejected event code must be answerable from the row alone."""
        bad = self._payload()
        bad["Eventcode"] = "NOSUCHCODE"
        resp = self._post(bad, key=self.raw_key)
        self.assertEqual(resp.status_code, 400, resp.content)

        log = WebhookLog.objects.get()
        self.assertEqual(log.status, WebhookLog.Status.FAILED)
        self.assertTrue(log.error_message, "a failed row with no reason is a dead end")
        self.assertIsNotNone(log.received_at)

    # ── The paths that used to write nothing ──────────────────────────────────

    def test_auth_failure_is_logged(self):
        resp = self._post(key="crm_live_not-a-real-key")
        self.assertEqual(resp.status_code, 401, resp.content)

        log = WebhookLog.objects.get()
        self.assertEqual(log.status, WebhookLog.Status.FAILED)
        self.assertEqual(log.http_status, 401)
        self.assertIsNotNone(log.received_at)

    def test_malformed_body_is_logged_not_swallowed(self):
        resp = self._post('{"InvoiceNumber": "INV-BAD", oops', key=self.raw_key)
        self.assertEqual(resp.status_code, 400, resp.content)

        log = WebhookLog.objects.get()
        self.assertEqual(log.status, WebhookLog.Status.FAILED)
        self.assertEqual(log.http_status, 400)
        self.assertIn("parse", log.error_message.lower())
        # The bytes the sender actually sent — otherwise the row cannot explain
        # itself, and request.body is unrecoverable once the parser has run.
        self.assertIn("oops", log.payload.get("_unparsed_body", ""))
        self.assertIsNotNone(log.received_at)

    def test_unauthenticated_malformed_body_logs_401_without_storing_the_body(self):
        """Auth is checked first, so a stranger cannot write into our logs."""
        resp = self._post("{broken", key="crm_live_not-a-real-key")
        self.assertEqual(resp.status_code, 401, resp.content)

        log = WebhookLog.objects.get()
        self.assertEqual(log.http_status, 401)
        self.assertNotIn("_unparsed_body", log.payload)

    def test_crash_mid_processing_marks_the_row_failed(self):
        """
        The row already exists when the processor blows up. It must not be left
        in `processing`, which reads in the UI as a delivery still in flight.
        """
        with patch("webhooks.views.WebhookProcessor.process", side_effect=RuntimeError("boom")):
            resp = self._post(key=self.raw_key)
        self.assertEqual(resp.status_code, 500, resp.content)

        log = WebhookLog.objects.get()
        self.assertEqual(log.status, WebhookLog.Status.FAILED)
        self.assertEqual(log.http_status, 500)
        self.assertEqual(log.processing_status, WebhookLog.ProcessingStatus.ERROR)
        self.assertIn("boom", log.error_message)
        self.assertTrue(log.stack_trace, "a 500 with no traceback cannot be diagnosed")
        self.assertIsNotNone(log.processed_at)

    def test_crash_does_not_create_a_second_row(self):
        with patch("webhooks.views.WebhookProcessor.process", side_effect=RuntimeError("boom")):
            self._post(key=self.raw_key)
        self.assertEqual(WebhookLog.objects.count(), 1)


class ReceivedAtOrderingTests(TestCase):
    """
    The Delivery logs table orders newest-first. Postgres sorts NULLs FIRST
    under DESC, so a nullable ordering column puts the oldest junk on page one
    and hides new deliveries below it.
    """

    def test_created_at_is_non_null_for_every_row(self):
        WebhookLog.objects.create(payload={}, headers={}, response={})
        self.assertFalse(WebhookLog.objects.filter(created_at__isnull=True).exists())

    def test_newest_first_by_created_at_puts_the_newest_row_first(self):
        older = WebhookLog.objects.create(payload={}, headers={}, response={})
        newer = WebhookLog.objects.create(payload={}, headers={}, response={})
        ids = list(WebhookLog.objects.order_by("-created_at").values_list("id", flat=True))
        self.assertEqual(ids[0], newer.id)
        self.assertIn(older.id, ids)
