"""
webhooks/tests_auth.py
───────────────────────
/api/webhooks/ingest/ authenticates on the KEY and nothing else.

THE HOLE: authenticate_request() used to fall back to matching Origin/Referer
against CORS_ALLOWED_ORIGINS when no key header was present. Origin and Referer
are ordinary request headers — only a browser is bound to send them truthfully,
and a webhook sender is not a browser. So this posted a booking with no key:

    curl -X POST .../ingest/ -H "Origin: https://one-of-our-sites.com"

The domain is public information, which made the whole fallback worth exactly
the secrecy of a hostname: nothing. It also scaled backwards — onboarding 300
sending websites would have meant adding 300 origins, and every origin added was
another key-less way in.

These tests pin the fallback shut. The spoof cases must stay 401 forever; the
key cases prove the fix did not cost us the senders that authenticate properly.
"""
from datetime import date

from django.test import TestCase, override_settings
from django.urls import reverse

from webhooks.models import WebhookApiKey, WebhookLog
from webhooks.tests_event_resolution import make_event

# An origin that IS on the CORS allow-list. That is the point: under the old code
# being on the list was sufficient to get in without a key, so the domain most
# useful for this test is a trusted one rather than an obviously hostile one.
LISTED_ORIGIN = "https://listed-site.example.com"


def payload(invoice="INV-AUTH-001"):
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


@override_settings(CORS_ALLOWED_ORIGINS=[LISTED_ORIGIN], WEBHOOK_SECRET_KEY="")
class OriginIsNotACredentialTests(TestCase):
    """A request with no key is rejected however its Origin/Referer is dressed."""

    @classmethod
    def setUpTestData(cls):
        make_event("TST - PM", web_bookings=True, event_date=date(2026, 2, 11))
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="test-suite", api_key=cls.raw_key)

    def _post(self, *, invoice="INV-AUTH-001", **headers):
        return self.client.post(
            reverse("webhook-ingest"), data=payload(invoice),
            content_type="application/json", **headers,
        )

    # ── The hole, held shut ───────────────────────────────────────────────────

    def test_spoofed_origin_on_the_cors_list_is_rejected(self):
        resp = self._post(HTTP_ORIGIN=LISTED_ORIGIN)
        self.assertEqual(resp.status_code, 401, resp.content)

    def test_spoofed_referer_on_the_cors_list_is_rejected(self):
        """Referer was the second half of the fallback and spoofs just as easily."""
        resp = self._post(HTTP_REFERER=f"{LISTED_ORIGIN}/checkout/thanks")
        self.assertEqual(resp.status_code, 401, resp.content)

    def test_no_credentials_at_all_is_rejected(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 401, resp.content)

    def test_rejection_names_the_header_the_sender_should_have_used(self):
        """
        At 300 senders the 401 body is the whole of most integrators' debugging,
        so it has to say which header carries the key — there are three in this
        codebase and only one works here.
        """
        resp = self._post(HTTP_ORIGIN=LISTED_ORIGIN)
        self.assertIn("X-CRM-API-KEY", resp.json().get("error", ""))

    def test_a_rejected_spoof_is_still_audited(self):
        """A key-less attempt must be visible in Delivery logs, not silently dropped."""
        self._post(HTTP_ORIGIN=LISTED_ORIGIN)
        log = WebhookLog.objects.get()
        self.assertEqual(log.http_status, 401)
        self.assertEqual(log.status, WebhookLog.Status.FAILED)
        self.assertTrue(log.error_message)

    def test_no_booking_is_created_by_a_spoofed_origin(self):
        from book_event.models import BookEvent
        self._post(HTTP_ORIGIN=LISTED_ORIGIN)
        self.assertFalse(BookEvent.objects.filter(invoice_number="INV-AUTH-001").exists())

    # ── What must keep working ────────────────────────────────────────────────

    def test_valid_key_still_ingests(self):
        resp = self._post(HTTP_X_CRM_API_KEY=self.raw_key)
        self.assertIn(resp.status_code, (200, 201), resp.content)

    def test_valid_key_is_enough_without_any_origin(self):
        """
        The normal shape of a real sender: a server-to-server POST carries no
        Origin at all. Removing the fallback must not have made one required.
        """
        resp = self._post(invoice="INV-AUTH-002", HTTP_X_CRM_API_KEY=self.raw_key)
        self.assertIn(resp.status_code, (200, 201), resp.content)

    def test_valid_key_with_an_unlisted_origin_still_ingests(self):
        """Origin is not consulted to grant, so an unrecognised one is irrelevant."""
        resp = self._post(invoice="INV-AUTH-003",
                          HTTP_X_CRM_API_KEY=self.raw_key,
                          HTTP_ORIGIN="https://not-on-any-list.example.org")
        self.assertIn(resp.status_code, (200, 201), resp.content)

    def test_inactive_key_is_rejected(self):
        WebhookApiKey.objects.update(is_active=False)
        resp = self._post(HTTP_X_CRM_API_KEY=self.raw_key)
        self.assertEqual(resp.status_code, 401, resp.content)

    def test_inactive_key_cannot_be_revived_by_a_listed_origin(self):
        """Revocation has to be final — it is the only lever we have per site."""
        WebhookApiKey.objects.update(is_active=False)
        resp = self._post(HTTP_X_CRM_API_KEY=self.raw_key, HTTP_ORIGIN=LISTED_ORIGIN)
        self.assertEqual(resp.status_code, 401, resp.content)


@override_settings(CORS_ALLOWED_ORIGINS=[LISTED_ORIGIN])
class LegacySecretTests(TestCase):
    """X-WEBHOOK-SECRET still works, and now reports its own failure."""

    @classmethod
    def setUpTestData(cls):
        make_event("TST - PM", web_bookings=True, event_date=date(2026, 2, 11))

    def _post(self, *, invoice="INV-SEC-001", **headers):
        return self.client.post(
            reverse("webhook-ingest"), data=payload(invoice),
            content_type="application/json", **headers,
        )

    @override_settings(WEBHOOK_SECRET_KEY="s3cret")
    def test_correct_secret_still_ingests(self):
        resp = self._post(HTTP_X_WEBHOOK_SECRET="s3cret")
        self.assertIn(resp.status_code, (200, 201), resp.content)

    @override_settings(WEBHOOK_SECRET_KEY="s3cret")
    def test_wrong_secret_is_rejected_and_says_so(self):
        """
        A wrong secret used to fall through to the Origin check and then be
        reported as the generic "authentication required" — which sent the
        integrator looking for a missing header they had actually sent.
        """
        resp = self._post(HTTP_X_WEBHOOK_SECRET="wrong")
        self.assertEqual(resp.status_code, 401, resp.content)
        self.assertIn("Invalid webhook secret", resp.json().get("error", ""))

    @override_settings(WEBHOOK_SECRET_KEY="s3cret")
    def test_wrong_secret_is_not_rescued_by_a_listed_origin(self):
        resp = self._post(HTTP_X_WEBHOOK_SECRET="wrong", HTTP_ORIGIN=LISTED_ORIGIN)
        self.assertEqual(resp.status_code, 401, resp.content)

    @override_settings(WEBHOOK_SECRET_KEY="")
    def test_unconfigured_server_rejects_rather_than_accepting_anything(self):
        resp = self._post(HTTP_X_WEBHOOK_SECRET="anything")
        self.assertEqual(resp.status_code, 401, resp.content)
        self.assertIn("not configured", resp.json().get("error", ""))


@override_settings(CORS_ALLOWED_ORIGINS=[LISTED_ORIGIN], WEBHOOK_SECRET_KEY="")
class AllowedDomainsNarrowsButNeverGrantsTests(TestCase):
    """
    `allowed_domains` on a key is a soft filter over browser-originated traffic.
    It narrows a key that is already valid; it never admits one that is not, and
    it is skipped entirely when no Origin is present — which is the normal case
    for a server-to-server sender. These tests fix that asymmetry in place so it
    is not later mistaken for a security boundary.
    """

    @classmethod
    def setUpTestData(cls):
        make_event("TST - PM", web_bookings=True, event_date=date(2026, 2, 11))
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(
            name="domain-bound", api_key=cls.raw_key,
            allowed_domains=["bound-site.example.com"],
        )

    def _post(self, *, invoice="INV-DOM-001", **headers):
        return self.client.post(
            reverse("webhook-ingest"), data=payload(invoice),
            content_type="application/json", **headers,
        )

    def test_matching_origin_passes(self):
        resp = self._post(HTTP_X_CRM_API_KEY=self.raw_key,
                          HTTP_ORIGIN="https://bound-site.example.com")
        self.assertIn(resp.status_code, (200, 201), resp.content)

    def test_mismatched_origin_is_rejected(self):
        resp = self._post(HTTP_X_CRM_API_KEY=self.raw_key,
                          HTTP_ORIGIN="https://someone-else.example.com")
        self.assertEqual(resp.status_code, 401, resp.content)

    def test_absent_origin_skips_the_check_entirely(self):
        """
        Documented, not endorsed: omitting the header bypasses allowed_domains.
        This is precisely why the key — not the domain — is the credential.
        """
        resp = self._post(invoice="INV-DOM-002", HTTP_X_CRM_API_KEY=self.raw_key)
        self.assertIn(resp.status_code, (200, 201), resp.content)

    def test_a_bound_domain_alone_grants_nothing(self):
        resp = self._post(HTTP_ORIGIN="https://bound-site.example.com")
        self.assertEqual(resp.status_code, 401, resp.content)
