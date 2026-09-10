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

    def initialize(self, access_token):
        """
        One MCP initialize call, against a freshly built app with lifespan run.

        THE OAUTH ENDPOINTS NEED NONE OF THIS; only /mcp does. The streamable
        transport starts its session manager in the ASGI startup event, and
        without it every request raises "Task group is not initialized".
        uvicorn sends that event in production, httpx2's transport does not.

        A FRESH APP EACH TIME, because a session manager refuses to run twice,
        and the one mounted at import time is shared by the whole test process.
        The mounted app's routing is covered separately in config/tests_mcp.py.
        """
        import anyio

        import mcp_server as mcp_server_module
        from mcp_server import mcp as server

        app = server.streamable_http_app(
            streamable_http_path="/mcp",
            transport_security=mcp_server_module.TRANSPORT_SECURITY,
        )

        async def call():
            started = anyio.Event()
            finished = anyio.Event()

            async def receive():
                if not started.is_set():
                    return {"type": "lifespan.startup"}
                await finished.wait()
                return {"type": "lifespan.shutdown"}

            async def send(message):
                if message["type"].startswith("lifespan.startup."):
                    started.set()

            async with anyio.create_task_group() as group:
                group.start_soon(
                    app, {"type": "lifespan", "asgi": {"version": "3.0"}},
                    receive, send,
                )
                await started.wait()
                try:
                    async with httpx2.AsyncClient(
                        transport=httpx2.ASGITransport(app=app),
                        base_url="https://www.app.iq-hub.com",
                    ) as client:
                        return await client.post("/mcp", json={
                            "jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-06-18",
                                "capabilities": {},
                                "clientInfo": {"name": "probe", "version": "1"},
                            },
                        }, headers={
                            "Accept": "application/json, text/event-stream",
                            "Authorization": f"Bearer {access_token}",
                        })
                finally:
                    finished.set()

        return async_to_sync(call)()

    def register(self, auth_method="none"):
        body = {
            "client_name": "Claude",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        }
        # None means "say nothing", which is what claude.ai does and is not the
        # same as asking for a public client. See register_like_claude.
        if auth_method is not None:
            body["token_endpoint_auth_method"] = auth_method
        response = self.mcp("POST", "/register", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        issued = response.json()
        return issued["client_id"], issued.get("client_secret")

    def register_like_claude(self):
        """
        Register the way claude.ai does, without naming an auth method.

        THE SDK THEN MAKES IT CONFIDENTIAL. register.py defaults a missing
        token_endpoint_auth_method to client_secret_post and mints a secret,
        and the authorization server metadata never advertises "none", so a
        client that follows the metadata ends up with a secret whether it
        wanted one or not. Registering with "none" by hand, which every
        earlier test did, exercised a path claude.ai never takes.
        """
        return self.register(auth_method=None)

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

    def exchange(self, client_id, code, verifier=VERIFIER, secret=None):
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
        }
        if secret:
            data["client_secret"] = secret
        return self.mcp("POST", "/token", data=data)

    # ── The whole thing, over HTTP ──────────────────────────────────────────

    def test_a_public_client_completes_the_flow_and_the_token_works(self):
        client_id, _ = self.register()
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

    def test_an_issued_token_is_accepted_by_the_mcp_endpoint(self):
        """
        THE STEP EVERY OTHER TEST SKIPS.

        The API tests prove the token opens DRF. The flow tests prove the
        provider issues it. Neither one sends it to /mcp, which is guarded by
        the SDK's own bearer middleware, with different rules; notably
        validate_token_resource compares the token's resource against
        resource_server_url. A token can therefore be perfectly valid to the
        CRM and still be refused by the transport.
        """
        client_id, _ = self.register()
        code = self.consent(self.authorize(client_id))
        access = self.exchange(client_id, code).json()["access_token"]

        response = self.initialize(access)
        self.assertNotEqual(
            response.status_code, 401,
            f"the MCP endpoint refused a token it issued: {response.text[:300]}")
        self.assertEqual(response.status_code, 200, response.text[:300])

    def test_a_token_issued_without_a_resource_is_still_accepted(self):
        """
        A client that omits the resource parameter must still work.

        RFC 8707's resource indicator is optional, and validate_token_resource
        rejects a token whose resource does not equal resource_server_url. If
        an absent resource is stored as an empty string and compared, every
        such token is refused; this pins the behaviour either way.
        """
        client_id, _ = self.register()
        response = self.mcp("GET", "/authorize", params={
            "response_type": "code", "client_id": client_id,
            "redirect_uri": REDIRECT, "code_challenge": CHALLENGE,
            "code_challenge_method": "S256", "state": "no-resource",
            "scope": "crm",
        })
        self.assertEqual(response.status_code, 302, response.text)
        tx = parse_qs(urlparse(response.headers["location"]).query)["tx"][0]
        access = self.exchange(client_id, self.consent(tx)).json()["access_token"]

        self.assertEqual(self.initialize(access).status_code, 200)

    def test_a_client_registering_the_way_claude_does_completes_the_flow(self):
        """
        THE FAILURE THAT REACHED PRODUCTION, and the one no earlier test could
        have caught, because they all registered with "none" by hand.

        claude.ai omits token_endpoint_auth_method. The SDK then defaults it to
        client_secret_post and mints a secret, so the client is confidential.
        Storing only a hash of that secret meant get_client() returned None,
        the SDK judged the client misconfigured, and /token refused it. Sign-in
        and consent both succeeded first, so the error appeared only when the
        browser came back to Claude.
        """
        client_id, secret = self.register_like_claude()
        self.assertTrue(secret, "the SDK should have issued a client secret")
        row = OAuthClient.objects.get()
        self.assertEqual(row.token_endpoint_auth_method, "client_secret_post")
        # Stored as issued, because the SDK compares it directly.
        self.assertEqual(row.client_secret, secret)

        code = self.consent(self.authorize(client_id))
        response = self.exchange(client_id, code, secret=secret)
        self.assertEqual(response.status_code, 200, response.text)

        access = response.json()["access_token"]
        self.assertEqual(self.initialize(access).status_code, 200)

    def test_a_confidential_client_with_the_wrong_secret_is_refused(self):
        # The secret has to be checked, not merely stored.
        client_id, _ = self.register_like_claude()
        code = self.consent(self.authorize(client_id))
        response = self.exchange(client_id, code, secret="not-the-secret")
        self.assertEqual(response.status_code, 401, response.text)
        self.assertFalse(IssuedToken.objects.exists())

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
        client_id, _ = self.register()
        code = self.consent(self.authorize(client_id))
        response = self.exchange(client_id, code, verifier="a" * 43)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(IssuedToken.objects.exists())

    def test_a_code_cannot_be_redeemed_twice(self):
        client_id, _ = self.register()
        code = self.consent(self.authorize(client_id))
        self.assertEqual(self.exchange(client_id, code).status_code, 200)
        self.assertEqual(self.exchange(client_id, code).status_code, 400)

    def test_a_code_cannot_be_redeemed_by_a_different_client(self):
        victim, _ = self.register()
        attacker, _ = self.register()
        code = self.consent(self.authorize(victim))
        self.assertEqual(self.exchange(attacker, code).status_code, 400)

    def test_an_unregistered_redirect_uri_is_refused_at_authorize(self):
        client_id, _ = self.register()
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
