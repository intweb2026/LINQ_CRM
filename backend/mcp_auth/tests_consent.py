"""
Tests for the MCP consent page.

The flow this covers is the one place a person's identity turns into an OAuth
authorization code, so the refusals matter more than the happy path. Google's
verifier is patched; accounts/tests_google_identity.py covers the rules it
applies.
"""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from mcp_auth.models import (
    AuthorizationCode,
    OAuthClient,
    PendingAuthorization,
    hash_secret,
)

User = get_user_model()
VERIFY = "accounts.google_identity.google_id_token.verify_oauth2_token"
TX = "a-parked-request-token"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


@override_settings(GOOGLE_OAUTH_CLIENT_ID="test-client-id",
                   GOOGLE_OAUTH_ALLOWED_DOMAINS=["iq-hub.com"])
class ConsentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(
            username="consent.tester", email="consent.tester@iq-hub.com",
            role="sales", is_active=True, login_access=True,
        )
        cls.client_row = OAuthClient.objects.create(
            client_id="client-abc", client_name="Claude",
            redirect_uris=[REDIRECT],
        )

    def setUp(self):
        self.pending = PendingAuthorization.objects.create(
            tx_hash=hash_secret(TX), client=self.client_row,
            redirect_uri=REDIRECT, code_challenge="a-challenge",
            state="opaque-state", scopes=["crm"],
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    def post(self, **kwargs):
        data = {"tx": TX, "credential": "a-credential"}
        data.update(kwargs)
        return self.client.post(reverse("mcp-auth-consent-complete"), data)

    def google_says(self, **claims):
        base = {"email": "consent.tester@iq-hub.com", "email_verified": True}
        base.update(claims)
        return patch(VERIFY, return_value=base)

    # ── The page ────────────────────────────────────────────────────────────

    def test_page_names_the_client_asking_for_access(self):
        response = self.client.get(reverse("mcp-auth-consent"), {"tx": TX})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Claude")

    def test_expired_handle_shows_a_message_rather_than_a_sign_in_button(self):
        self.pending.expires_at = timezone.now() - timedelta(seconds=1)
        self.pending.save(update_fields=["expires_at"])
        response = self.client.get(reverse("mcp-auth-consent"), {"tx": TX})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "expired")
        self.assertNotContains(response, "gbtn")

    # ── Completing it ───────────────────────────────────────────────────────

    def test_success_mints_a_code_and_redirects_with_the_original_state(self):
        with self.google_says():
            response = self.post()
        self.assertEqual(response.status_code, 200)

        target = urlparse(response.json()["redirect"])
        query = parse_qs(target.query)
        self.assertEqual(f"{target.scheme}://{target.netloc}{target.path}", REDIRECT)
        # state comes from the parked row, not from anything the browser sent.
        self.assertEqual(query["state"], ["opaque-state"])

        code = AuthorizationCode.objects.get()
        self.assertEqual(code.user, self.user)
        self.assertEqual(code.code_hash, hash_secret(query["code"][0]))
        # The PKCE challenge has to survive, or the token exchange cannot bind
        # to the browser that started this.
        self.assertEqual(code.code_challenge, "a-challenge")

    def test_the_parked_request_is_consumed_so_it_cannot_be_completed_twice(self):
        with self.google_says():
            self.post()
        self.assertFalse(PendingAuthorization.objects.exists())
        with self.google_says():
            second = self.post()
        self.assertEqual(second.status_code, 400)
        self.assertEqual(AuthorizationCode.objects.count(), 1)

    def test_unknown_handle_mints_nothing(self):
        with self.google_says():
            response = self.post(tx="not-a-real-handle")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AuthorizationCode.objects.exists())

    def test_refused_identity_mints_nothing(self):
        # An account nobody has provisioned. The refusal must not leave a code
        # behind, and must not consume the parked request either.
        with self.google_says(email="ghost@iq-hub.com"):
            response = self.post()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AuthorizationCode.objects.exists())
        self.assertTrue(PendingAuthorization.objects.exists())

    def test_invalid_google_credential_mints_nothing(self):
        with patch(VERIFY, side_effect=ValueError("bad token")):
            response = self.post()
        self.assertEqual(response.status_code, 401)
        self.assertFalse(AuthorizationCode.objects.exists())

    def test_redirect_uri_comes_from_the_parked_row_not_the_request(self):
        # The classic attack. Sending a different redirect_uri must not move
        # where the code is delivered.
        with self.google_says():
            response = self.post(redirect_uri="https://evil.example/steal")
        self.assertTrue(response.json()["redirect"].startswith(REDIRECT))

    def test_a_redirect_uri_with_its_own_query_string_keeps_it(self):
        self.pending.redirect_uri = REDIRECT + "?tenant=iqhub"
        self.pending.save(update_fields=["redirect_uri"])
        with self.google_says():
            response = self.post()
        query = parse_qs(urlparse(response.json()["redirect"]).query)
        self.assertEqual(query["tenant"], ["iqhub"])
        self.assertIn("code", query)

    def test_get_is_rejected_on_the_completion_endpoint(self):
        response = self.client.get(reverse("mcp-auth-consent-complete"))
        self.assertEqual(response.status_code, 405)
