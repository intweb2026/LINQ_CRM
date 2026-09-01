"""
webhooks/tests_key_target.py
─────────────────────────────
WebhookApiKey.target scopes a key to one ingest endpoint, opt-in from both ends.

THE THING THAT MUST NEVER BREAK IS THE FIRST TEST CLASS. Every key in production
was issued before this column existed, so every one of them reads target = ""
and MUST keep posting to every endpoint. The column was added by a single
ALTER TABLE ... ADD COLUMN with a default of "", no row was rewritten, and if a
blank target ever starts refusing a delivery then every live website integration
stops at once. That is the blunder these tests exist to prevent, so they assert
"not 401" rather than a processor's success code: what is under test is the
credential check, and it has to hold whatever the body happens to be.

The narrowing itself is deliberately weak: it refuses only when the KEY names a
target AND the endpoint names a different one.
"""
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from accounts.permissions import dapi_USERNAME
from webhooks.models import WebhookApiKey

BOOKINGS     = reverse("webhook-ingest")
TICKETS      = reverse("webhook-ingest-tickets")
PAPER_REVIEW = reverse("webhook-ingest-paper-review")

TICKET_BODY = {"purpose": "AS", "type_of_ticket": "Blue - BX"}


def key(**over):
    fields = {"name": "k", "api_key": WebhookApiKey.generate_key()}
    fields.update(over)
    return WebhookApiKey.objects.create(**fields)


class UnscopedKeysReachEveryEndpoint(TestCase):
    """The production shape: target = "", the value every existing row holds."""

    def setUp(self):
        self.key = key(name="website-prod")

    def _post(self, url):
        return self.client.post(url, TICKET_BODY, content_type="application/json",
                                HTTP_X_CRM_API_KEY=self.key.api_key)

    def test_the_column_defaults_to_unrestricted(self):
        self.assertEqual(self.key.target, "")

    def test_an_unscoped_key_authenticates_on_all_three_endpoints(self):
        for url in (BOOKINGS, TICKETS, PAPER_REVIEW):
            with self.subTest(url=url):
                # Not 401 is the whole claim. The body is a ticket body, so two
                # of the three will reject it on content — that is the
                # processor's business, not the credential's.
                self.assertNotEqual(self._post(url).status_code, 401)

    def test_an_unscoped_key_still_creates_a_ticket(self):
        self.assertEqual(self._post(TICKETS).status_code, 201)

    def test_liveness_passes_on_every_endpoint_for_an_unscoped_key(self):
        for url in (BOOKINGS, TICKETS, PAPER_REVIEW):
            with self.subTest(url=url):
                resp = self.client.get(url, HTTP_X_CRM_API_KEY=self.key.api_key)
                self.assertEqual(resp.status_code, 200)


class ScopedKeysAreNarrowed(TestCase):
    def _post(self, url, api_key):
        return self.client.post(url, TICKET_BODY, content_type="application/json",
                                HTTP_X_CRM_API_KEY=api_key)

    def test_a_ticket_key_posts_tickets_and_nothing_else(self):
        k = key(name="zoho-tickets", target=WebhookApiKey.Target.TICKETS)
        self.assertEqual(self._post(TICKETS, k.api_key).status_code, 201)
        for url in (BOOKINGS, PAPER_REVIEW):
            with self.subTest(url=url):
                resp = self._post(url, k.api_key)
                self.assertEqual(resp.status_code, 401)
                self.assertIn("scoped to Tickets", resp.json()["error"])

    def test_a_booking_key_cannot_post_tickets(self):
        k = key(name="website-only", target=WebhookApiKey.Target.BOOKINGS)
        self.assertEqual(self._post(TICKETS, k.api_key).status_code, 401)
        self.assertNotEqual(self._post(BOOKINGS, k.api_key).status_code, 401)

    def test_a_refused_scope_is_not_counted_as_usage(self):
        k = key(target=WebhookApiKey.Target.BOOKINGS)
        self._post(TICKETS, k.api_key)
        k.refresh_from_db()
        self.assertEqual(k.usage_count, 0)

    def test_liveness_is_refused_off_scope_too(self):
        k = key(target=WebhookApiKey.Target.TICKETS)
        self.assertEqual(
            self.client.get(BOOKINGS, HTTP_X_CRM_API_KEY=k.api_key).status_code, 401)


class IngestPathIsResolvedNotTyped(TestCase):
    """The keys page builds its copy-URL from this, so it is API surface."""

    def test_each_target_resolves_to_its_own_route(self):
        self.assertEqual(key(target="").ingest_path(), BOOKINGS)
        self.assertEqual(key(target=WebhookApiKey.Target.BOOKINGS).ingest_path(), BOOKINGS)
        self.assertEqual(key(target=WebhookApiKey.Target.TICKETS).ingest_path(), TICKETS)
        self.assertEqual(
            key(target=WebhookApiKey.Target.PAPER_REVIEW).ingest_path(), PAPER_REVIEW)

    def test_an_unmapped_target_falls_back_instead_of_raising(self):
        # A credential must stay listable even if someone adds a choice and
        # forgets the route; a 500 on the keys page is worse than a wrong path.
        self.assertEqual(key(target="something-new").ingest_path(), BOOKINGS)


class KeysApiCarriesTheTarget(TestCase):
    def setUp(self):
        self.hp = User.objects.create_user(
            username=dapi_USERNAME, password="x", role="admin")
        self.client.force_login(self.hp)

    def test_create_accepts_a_target_and_the_list_serves_it_with_the_path(self):
        resp = self.client.post(
            "/api/webhooks/keys/",
            {"name": "zoho-tickets", "event": "", "target": "tickets"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)

        rows = self.client.get("/api/webhooks/keys/").json()
        row  = (rows["results"] if isinstance(rows, dict) else rows)[0]
        self.assertEqual(row["target"], "tickets")
        self.assertEqual(row["ingest_path"], TICKETS)

    def test_omitting_the_target_creates_an_unrestricted_key(self):
        self.client.post("/api/webhooks/keys/", {"name": "legacy-shape", "event": ""},
                         content_type="application/json")
        self.assertEqual(WebhookApiKey.objects.get(name="legacy-shape").target, "")
