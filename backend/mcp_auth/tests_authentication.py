"""
Tests for authenticating against the CRM API with an MCP access token.

THE GAP THESE EXIST FOR. The MCP tools call the CRM's own HTTP API. Before
McpTokenAuthentication the credential they held was an OAuth access token
presented as "Token …", which DRF looked up in authtoken_token and never found,
so every tool call answered 401. These tests hit a real API endpoint with a
real MCP token, so the whole chain is exercised rather than the lookup alone.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import OperationalError, ProgrammingError
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token

from mcp_auth.models import IssuedToken, OAuthClient, hash_secret

User = get_user_model()
RAW = "an-mcp-access-token"
# The caller's own effective permission matrix. Chosen because every
# authenticated user may read it, so a non-200 here is an authentication
# failure and never the CRM's permission grid answering 403, which is what a
# resource endpoint like /api/events/ would have conflated it with.
ENDPOINT = "/api/users/my-permissions/"


class McpTokenAuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(
            username="mcp.caller", email="mcp.caller@iq-hub.com",
            role="admin", is_active=True, login_access=True,
        )
        cls.oauth_client = OAuthClient.objects.create(
            client_id="client-abc", client_name="Claude",
        )

    def setUp(self):
        self.token = IssuedToken.objects.create(
            token_hash=hash_secret(RAW), kind=IssuedToken.ACCESS,
            client=self.oauth_client, user=self.user, scopes=["crm"],
            expires_at=timezone.now() + timedelta(hours=1),
        )

    def get(self, raw=RAW, scheme="Bearer"):
        return self.client.get(ENDPOINT, HTTP_AUTHORIZATION=f"{scheme} {raw}")

    def test_a_valid_mcp_token_reaches_the_api(self):
        self.assertEqual(self.get().status_code, 200)

    def test_the_request_is_attributed_to_the_tokens_owner(self):
        # The whole point of per-user OAuth. If this ever resolves to a service
        # identity, the audit trail stops naming real people.
        response = self.get()
        self.assertEqual(response.wsgi_request.user, self.user)

    def test_no_credential_is_still_refused(self):
        self.assertEqual(self.client.get(ENDPOINT).status_code, 401)

    def test_an_unknown_bearer_token_is_refused(self):
        self.assertEqual(self.get(raw="not-a-real-token").status_code, 401)

    def test_an_expired_token_is_refused(self):
        self.token.expires_at = timezone.now() - timedelta(seconds=1)
        self.token.save(update_fields=["expires_at"])
        self.assertEqual(self.get().status_code, 401)

    def test_a_refresh_token_cannot_be_used_as_an_access_token(self):
        self.token.kind = IssuedToken.REFRESH
        self.token.save(update_fields=["kind"])
        self.assertEqual(self.get().status_code, 401)

    def test_deactivating_the_account_cuts_off_a_live_token(self):
        # status drives is_active through User.save(). Revocation must not wait
        # for the token to expire.
        self.user.status = User.Status.INACTIVE
        self.user.save(update_fields=["status"])
        self.assertEqual(self.get().status_code, 401)

    def test_an_mcp_token_presented_as_a_drf_token_is_refused(self):
        # The exact shape of the original bug. The two schemes read different
        # tables, so sending one under the other's name must fail rather than
        # quietly succeed.
        self.assertEqual(self.get(scheme="Token").status_code, 401)

    def test_drf_token_authentication_still_works_alongside_it(self):
        # Adding an authenticator must not disturb the one the web app uses.
        drf = Token.objects.create(user=self.user)
        self.assertEqual(self.get(raw=drf.key, scheme="Token").status_code, 200)

    def _lookup_raises(self, exception):
        """Stand in for the model so the token lookup fails the way we choose."""
        model = MagicMock()
        model.ACCESS = IssuedToken.ACCESS
        model.objects.select_related.return_value.filter.return_value.first.side_effect = exception
        return patch("mcp_auth.authentication.IssuedToken", model)

    def test_a_missing_table_answers_401_rather_than_500(self):
        """
        Code deployed ahead of its migration must not break the API.

        This is not hypothetical. It reached production once; every request
        carrying a Bearer header answered 500 because the mcp_auth tables did
        not exist yet. No table means no tokens were ever issued, so refusing
        the token is the honest answer.
        """
        with self._lookup_raises(ProgrammingError('relation "x" does not exist')):
            with self.assertLogs("mcp_auth.authentication", level="ERROR") as logged:
                response = self.get()
        self.assertEqual(response.status_code, 401)
        # Loud, because somebody still has to run migrate.
        self.assertIn("migrate", logged.output[0])

    def test_a_missing_table_does_not_break_drf_token_auth(self):
        # The web app must keep working through the same window.
        drf = Token.objects.create(user=self.user)
        with self._lookup_raises(ProgrammingError('relation "x" does not exist')):
            response = self.get(raw=drf.key, scheme="Token")
        self.assertEqual(response.status_code, 200)

    def test_a_dead_database_is_not_swallowed(self):
        # OperationalError is an outage, not a deploy-order mistake. Turning it
        # into a tidy 401 would hide a broken database behind a wall of
        # plausible-looking auth failures.
        with self._lookup_raises(OperationalError("connection refused")):
            with self.assertRaises(OperationalError):
                self.get()

    def test_unauthenticated_401s_still_advertise_the_token_scheme(self):
        # DRF takes WWW-Authenticate from the FIRST authenticator. Appending
        # ours must not change what existing clients are told.
        response = self.client.get(ENDPOINT)
        self.assertEqual(response["WWW-Authenticate"], "Token")
