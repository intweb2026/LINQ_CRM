"""
webhooks/tests_ticket_ingest.py
────────────────────────────────
POST /api/webhooks/tickets/ — the same key that posts a booking posts a ticket.

What is pinned here is the part that cannot be seen from either side alone:
TicketCreateSerializer.create() used to read self.context["request"].user and
stamp user.id onto mr_submitted_by, which a webhook — having no user at all —
could only satisfy with AnonymousUser, and no FK accepts that. So the creator
columns must come out NULL while the UI path keeps stamping the real user, and
a redelivery of the same external_id must not mint a second ticket.
"""
from django.test import TestCase
from django.urls import reverse

from ticket_central.models import Ticket
from webhooks.models import WebhookApiKey, WebhookLog

URL = reverse("webhook-ingest-tickets")


def payload(**over):
    body = {
        "purpose":        "AS",
        "type_of_ticket": "Blue - BX",
        "event_code":     "TST - PM",
        "event_name":     "Test Summit",
        "organizer":      "Someone Ltd",
    }
    body.update(over)
    return body


class TicketWebhookTests(TestCase):
    def setUp(self):
        self.key = WebhookApiKey.objects.create(
            name="Zoho Tickets", api_key=WebhookApiKey.generate_key(),
        )

    def _post(self, body, **extra):
        return self.client.post(URL, body, content_type="application/json", **extra)

    def _auth(self, body):
        return self._post(body, HTTP_X_CRM_API_KEY=self.key.api_key)

    # ── Auth ─────────────────────────────────────────────────────────────────
    def test_no_key_is_rejected_and_logged(self):
        resp = self._post(payload())
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(Ticket.objects.count(), 0)
        log = WebhookLog.objects.get()
        self.assertEqual(log.status, WebhookLog.Status.FAILED)
        self.assertEqual(log.http_status, 401)

    def test_inactive_key_is_rejected(self):
        self.key.is_active = False
        self.key.save(update_fields=["is_active"])
        self.assertEqual(self._auth(payload()).status_code, 401)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_key_in_the_query_string_also_works(self):
        resp = self.client.post(
            f"{URL}?X-CRM-API-KEY={self.key.api_key}",
            payload(), content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertIn("[url-auth]", WebhookLog.objects.get().source)

    # ── Create ───────────────────────────────────────────────────────────────
    def test_valid_delivery_creates_a_numbered_mr_submitted_ticket(self):
        resp = self._auth(payload())
        self.assertEqual(resp.status_code, 201, resp.data)

        ticket = Ticket.objects.get(pk=resp.data["ticket_id"])
        self.assertEqual(ticket.status, Ticket.Status.MR_SUBMITTED)
        self.assertEqual(ticket.event_code, "TST - PM")
        # Minted at create, format "TYPE-PURPOSE NUMBER".
        self.assertTrue(ticket.ticket_number.startswith("BX-AS "))
        # The whole point: no user on the request, so no user on the row.
        self.assertIsNone(ticket.created_by)
        self.assertIsNone(ticket.mr_submitted_by)
        self.assertIsNotNone(ticket.mr_submitted_at)

        log = WebhookLog.objects.get()
        self.assertEqual(log.status, WebhookLog.Status.SUCCESS)
        self.assertEqual(log.records_inserted, 1)
        self.assertEqual(log.api_key_id, self.key.id)
        self.assertEqual(log.invoice_number, ticket.ticket_number)
        self.assertIn("tickets:", log.source)

    def test_zoho_flow_wrapping_is_unwrapped(self):
        resp = self._auth({"webhookTrigger": {"payload": payload()}})
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Ticket.objects.count(), 1)

    def test_missing_purpose_is_a_400_not_a_500(self):
        resp = self._auth(payload(purpose=""))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Ticket.objects.count(), 0)
        self.assertEqual(WebhookLog.objects.get().http_status, 400)

    def test_data_mining_fields_are_refused(self):
        resp = self._auth(payload(actual_number=5))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Ticket.objects.count(), 0)

    # ── Idempotency ──────────────────────────────────────────────────────────
    def test_redelivery_of_the_same_external_id_makes_no_second_ticket(self):
        first = self._auth(payload(external_id="ZOHO-1"))
        self.assertEqual(first.status_code, 201)

        again = self._auth(payload(external_id="ZOHO-1"))
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.data["duplicate"])
        self.assertEqual(again.data["ticket_id"], first.data["ticket_id"])
        self.assertEqual(Ticket.objects.count(), 1)
        self.assertEqual(
            WebhookLog.objects.filter(status=WebhookLog.Status.DUPLICATE).count(), 1
        )

    def test_external_id_is_stored_so_the_dedupe_has_something_to_match(self):
        self._auth(payload(external_id="ZOHO-2"))
        self.assertEqual(Ticket.objects.get().external_id, "ZOHO-2")

    def test_two_deliveries_without_an_external_id_are_two_tickets(self):
        self._auth(payload())
        self._auth(payload())
        self.assertEqual(Ticket.objects.count(), 2)

    # ── Liveness ─────────────────────────────────────────────────────────────
    def test_get_is_a_liveness_check_that_writes_no_log(self):
        resp = self.client.get(URL, HTTP_X_CRM_API_KEY=self.key.api_key)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["key_name"], "Zoho Tickets")
        self.assertEqual(WebhookLog.objects.count(), 0)
        self.assertEqual(Ticket.objects.count(), 0)
