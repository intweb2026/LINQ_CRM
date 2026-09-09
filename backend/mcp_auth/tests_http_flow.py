"""
The OAuth flow driven over real HTTP, through the MCP app's own middleware.

WHY THIS EXISTS SEPARATELY FROM tests_flow.py
Those tests call the provider directly. That is useful, and it is also exactly
how a real bug got through: the SDK authenticates the client in middleware
BEFORE the provider is ever consulted, so a provider that returns a malformed
client looks perfectly healthy to a direct call and fails at /token in
production. It did. get_client() was omitting token_endpoint_auth_method, the
middleware saw a Python None, and every token exchange answered
"Unsupported auth method: None".

So this file speaks HTTP to the mounted app, the way a connector does, and
nothing here reaches past the front door on its own.
"""
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx2
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from mcp_auth.models import IssuedToken, OAuthClient

User = get_user_model()
VERIFY = "accounts.google_identity.google_id_token.verify_oauth2_token"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
EMAIL = "http.flow@iq-hub.com"
# From RFC 7636's own example, so the pair is known-good rather than home made.
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


@override_settings(GOOGLE_OAUTH_CLIENT_ID="test-client-id",
                   GOOGLE_OAUTH_ALLOWED_DOMAINS=["iq-hub.com"])
class OAuthOverHttpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(
            username="http.flow", email=EMAIL, role="sales",
            is_active=True, login_access=True,
        )

    def mcp(self, method, path, **kwargs):
        """
        One request against the mounted MCP app, in process, over ASGI.

        Driven through async_to_sync because httpx2's ASGI transport is
        async-only. The provider's own ORM work goes back through
        sync_to_async, which is thread sensitive and so stays on this thread
        and inside the test's transaction.
        """
        from config.asgi import mcp_application

        async def call():
            transport = httpx2.ASGITransport(app=mcp_application)
            async with httpx2.AsyncClient(
                transport=transport, base_url="https://www.app.iq-hub.com",
            ) as client:
                return await client.request(method, path, **kwargs)

        return async_to_sync(call)()

    def register(self, auth_method="none"):
        response = self.mcp("POST", "/register", json={
            "client_name": "Claude",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": auth_method,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["client_id"]

    def authorize(self, client_id):
        response = self.mcp("GET", "/authorize", params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "state": "opaque-state",
            "scope": "crm",
        })
        self.assertEqual(response.status_code, 302, response.text)
        return parse_qs(urlparse(response.headers["location"]).query)["tx"][0]

    def consent(self, tx, email=EMAIL):
        with patch(VERIFY, return_value={"email": email, "email_verified": True}):
            response = self.client.post(reverse("mcp-auth-consent-complete"),
                                        {"tx": tx, "credential": "a-credential"})
        self.assertEqual(response.status_code, 200, response.content)
        return parse_qs(urlparse(response.json()["redirect"]).query)["code"][0]

    def exchange(self, client_id, code, verifier=VERIFIER):
        return self.mcp("POST", "/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
        })

    # ── The whole thing, over HTTP ──────────────────────────────────────────

    def test_a_public_client_completes_the_flow_and_the_token_works(self):
        client_id = self.register()
        self.assertEqual(
            OAuthClient.objects.get().token_endpoint_auth_method, "none")

        code = self.consent(self.authorize(client_id))
        response = self.exchange(client_id, code)
        self.assertEqual(response.status_code, 200, response.text)

        tokens = response.json()
        self.assertEqual(tokens["token_type"], "Bearer")
        self.assertTrue(tokens["refresh_token"])

        # The end of the chain. The token has to open the CRM API.
        api = self.client.get("/api/users/my-permissions/",
                              HTTP_AUTHORIZATION=f"Bearer {tokens['access_token']}")
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.wsgi_request.user, self.user)

    def test_the_discovery_document_names_the_endpoints_that_exist(self):
        meta = self.mcp("GET", "/.well-known/oauth-authorization-server").json()
        for key in ("authorization_endpoint", "token_endpoint",
                    "registration_endpoint", "revocation_endpoint"):
            path = urlparse(meta[key]).path
            self.assertNotEqual(
                self.mcp("GET", path).status_code, 404,
                f"{key} advertises {path}, which is not mounted")

    def test_a_wrong_pkce_verifier_is_refused(self):
        # PKCE is the only thing binding the exchange to the browser that
        # started it, since a public client has no secret.
        client_id = self.register()
        code = self.consent(self.authorize(client_id))
        response = self.exchange(client_id, code, verifier="a" * 43)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(IssuedToken.objects.exists())

    def test_a_code_cannot_be_redeemed_twice(self):
        client_id = self.register()
        code = self.consent(self.authorize(client_id))
        self.assertEqual(self.exchange(client_id, code).status_code, 200)
        self.assertEqual(self.exchange(client_id, code).status_code, 400)

    def test_a_code_cannot_be_redeemed_by_a_different_client(self):
        victim = self.register()
        attacker = self.register()
        code = self.consent(self.authorize(victim))
        self.assertEqual(self.exchange(attacker, code).status_code, 400)

    def test_an_unregistered_redirect_uri_is_refused_at_authorize(self):
        client_id = self.register()
        response = self.mcp("GET", "/authorize", params={
            "response_type": "code", "client_id": client_id,
            "redirect_uri": "https://evil.example/steal",
            "code_challenge": CHALLENGE, "code_challenge_method": "S256",
            "scope": "crm",
        })
        self.assertNotEqual(response.status_code, 302)

    def test_the_mcp_endpoint_refuses_an_anonymous_call(self):
        response = self.mcp("POST", "/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        }, headers={"Accept": "application/json, text/event-stream"})
        self.assertEqual(response.status_code, 401)
        # The header a client follows to find out where to authenticate.
        self.assertIn("resource_metadata", response.headers["www-authenticate"])
