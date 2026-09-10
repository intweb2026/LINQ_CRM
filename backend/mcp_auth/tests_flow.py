"""
End to end test of the OAuth flow, walked the way a connector walks it.

Every other test file here checks one piece. This one checks that the pieces
join up, because each of them can be individually correct while the flow still
fails; a code that is minted but not exchangeable, or a token that is issued
but not accepted by the API, passes its own unit test perfectly well.

The steps mirror what claude.ai does.

  1. register itself, dynamic client registration
  2. get sent to the consent page and sign in with Google
  3. exchange the resulting code for tokens
  4. call the CRM API with the access token
  5. refresh, and find the old refresh token dead

Google's verifier is patched. Everything else is real, including the HTTP
round trip through DRF in step 4.
"""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from mcp_auth.models import IssuedToken, OAuthClient, PendingAuthorization
from mcp_auth.provider import DjangoOAuthProvider

User = get_user_model()
VERIFY = "accounts.google_identity.google_id_token.verify_oauth2_token"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
EMAIL = "flow.tester@iq-hub.com"


@override_settings(GOOGLE_OAUTH_CLIENT_ID="test-client-id",
                   GOOGLE_OAUTH_ALLOWED_DOMAINS=["iq-hub.com"])
class OAuthFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(
            username="flow.tester", email=EMAIL, role="sales",
            is_active=True, login_access=True,
        )

    def setUp(self):
        self.provider = DjangoOAuthProvider()

    # ── The steps, as helpers, so each test can stop where it likes ─────────

    def register(self):
        info = OAuthClientInformationFull(
            client_id="claude-generated-id",
            client_name="Claude",
            redirect_uris=[AnyUrl(REDIRECT)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope="crm",
        )
        async_to_sync(self.provider.register_client)(info)
        return async_to_sync(self.provider.get_client)("claude-generated-id")

    def authorize(self, client):
        url = async_to_sync(self.provider.authorize)(
            client,
            AuthorizationParams(
                state="opaque-state",
                scopes=["crm"],
                code_challenge="a-pkce-challenge",
                redirect_uri=AnyUrl(REDIRECT),
                redirect_uri_provided_explicitly=True,
                resource=f"{'https://www.app.iq-hub.com'}/mcp",
            ),
        )
        return parse_qs(urlparse(url).query)["tx"][0]

    def consent(self, tx, email=EMAIL):
        with patch(VERIFY, return_value={"email": email, "email_verified": True}):
            response = self.client.post(reverse("mcp-auth-consent-complete"),
                                        {"tx": tx, "credential": "a-credential"})
        return response

    def exchange(self, client, code):
        loaded = async_to_sync(self.provider.load_authorization_code)(client, code)
        self.assertIsNotNone(loaded, "the freshly minted code did not load back")
        return async_to_sync(self.provider.exchange_authorization_code)(client, loaded)

    # ── The whole thing ─────────────────────────────────────────────────────

    def test_a_connector_can_register_consent_exchange_and_call_the_api(self):
        client = self.register()
        self.assertEqual(OAuthClient.objects.count(), 1)

        tx = self.authorize(client)
        self.assertEqual(PendingAuthorization.objects.count(), 1)

        response = self.consent(tx)
        self.assertEqual(response.status_code, 200, response.content)
        code = parse_qs(urlparse(response.json()["redirect"]).query)["code"][0]

        tokens = self.exchange(client, code)
        self.assertEqual(tokens.token_type, "Bearer")
        self.assertTrue(tokens.refresh_token)

        # The payoff. The access token has to work against the real API, which
        # is the step that was broken before McpTokenAuthentication existed.
        api = self.client.get("/api/users/my-permissions/",
                              HTTP_AUTHORIZATION=f"Bearer {tokens.access_token}")
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.wsgi_request.user, self.user)

    def test_the_code_is_single_use(self):
        client = self.register()
        code = parse_qs(urlparse(
            self.consent(self.authorize(client)).json()["redirect"]).query)["code"][0]
        self.exchange(client, code)
        # Deleted rather than flagged, so a replay finds nothing at all.
        self.assertIsNone(
            async_to_sync(self.provider.load_authorization_code)(client, code))

    def test_refreshing_rotates_and_kills_the_old_token(self):
        client = self.register()
        code = parse_qs(urlparse(
            self.consent(self.authorize(client)).json()["redirect"]).query)["code"][0]
        first = self.exchange(client, code)

        loaded = async_to_sync(self.provider.load_refresh_token)(
            client, first.refresh_token)
        second = async_to_sync(self.provider.exchange_refresh_token)(
            client, loaded, ["crm"])

        self.assertNotEqual(second.refresh_token, first.refresh_token)
        # A stolen refresh token must be usable once at most.
        self.assertIsNone(async_to_sync(self.provider.load_refresh_token)(
            client, first.refresh_token))
        # And the access token it minted dies with it, by cascade.
        stale = self.client.get("/api/users/my-permissions/",
                                HTTP_AUTHORIZATION=f"Bearer {first.access_token}")
        self.assertEqual(stale.status_code, 401)
        fresh = self.client.get("/api/users/my-permissions/",
                                HTTP_AUTHORIZATION=f"Bearer {second.access_token}")
        self.assertEqual(fresh.status_code, 200)

    def test_a_refresh_cannot_widen_the_scopes_that_were_consented_to(self):
        client = self.register()
        code = parse_qs(urlparse(
            self.consent(self.authorize(client)).json()["redirect"]).query)["code"][0]
        first = self.exchange(client, code)
        loaded = async_to_sync(self.provider.load_refresh_token)(
            client, first.refresh_token)

        widened = async_to_sync(self.provider.exchange_refresh_token)(
            client, loaded, ["crm", "admin", "everything"])
        self.assertEqual(widened.scope, "crm")

    def test_an_unprovisioned_google_account_never_reaches_a_token(self):
        client = self.register()
        tx = self.authorize(client)
        response = self.consent(tx, email="stranger@iq-hub.com")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(IssuedToken.objects.exists())
        # The parked request survives, so the right person can still finish it.
        self.assertTrue(PendingAuthorization.objects.exists())

    def test_deactivating_the_person_cuts_off_their_live_connector(self):
        client = self.register()
        code = parse_qs(urlparse(
            self.consent(self.authorize(client)).json()["redirect"]).query)["code"][0]
        tokens = self.exchange(client, code)

        self.user.status = User.Status.INACTIVE
        self.user.save(update_fields=["status"])

        api = self.client.get("/api/users/my-permissions/",
                              HTTP_AUTHORIZATION=f"Bearer {tokens.access_token}")
        self.assertEqual(api.status_code, 401)
        # And the provider agrees, so the MCP transport refuses it too rather
        # than only the API refusing what the tools ask for.
        self.assertIsNone(
            async_to_sync(self.provider.load_access_token)(tokens.access_token))

    def test_an_expired_parked_request_cannot_be_completed(self):
        client = self.register()
        tx = self.authorize(client)
        PendingAuthorization.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.consent(tx).status_code, 400)
        self.assertFalse(IssuedToken.objects.exists())
