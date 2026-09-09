"""
Tests for the MCP endpoint's bearer token verifier.

Scope is deliberately narrow. The routing in config/asgi.py and the tool layer
in mcp_server.py are exercised by running the server; what needs a test is
DrfTokenVerifier, because it is the only thing deciding whether a caller gets
into the CRM at all, and because two of its three answers are refusals that no
happy path would notice were missing.
"""
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token

from mcp_server import PUBLIC_URL, DrfTokenVerifier

User = get_user_model()


class AsgiRoutingTests(TestCase):
    """
    The ASGI router has to forward every path the MCP app owns, not just /mcp.

    This failed silently in production first time round. The OAuth discovery
    document fell through to Django, where the React catch-all answered it with
    the frontend and a 200, so nothing looked broken until a client tried to
    authenticate.
    """

    def test_every_mcp_route_is_forwarded(self):
        from config.asgi import MCP_PATHS, mcp_application

        owned = {r.path for r in mcp_application.routes if hasattr(r, "path")}
        self.assertTrue(owned, "the MCP app exposed no routes at all")
        self.assertEqual(owned - MCP_PATHS, set(),
                         "these MCP routes would fall through to Django")

    def test_discovery_document_is_among_them(self):
        # Named explicitly as well as derived above. If a future SDK stops
        # serving this, discovery breaks and the derived check would not notice.
        from config.asgi import MCP_PATHS

        self.assertIn("/.well-known/oauth-protected-resource/mcp", MCP_PATHS)


class DrfTokenVerifierTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="mcp.tester", role="sales",
                                       is_active=True)
        cls.token = Token.objects.create(user=cls.user)

    def verify(self, raw):
        return async_to_sync(DrfTokenVerifier().verify_token)(raw)

    def test_valid_token_authenticates_as_its_owner(self):
        access = self.verify(self.token.key)
        self.assertIsNotNone(access)
        # subject is what the audit trail and any later per-user scoping read,
        # so it has to be the owner rather than a service identity.
        self.assertEqual(access.subject, "mcp.tester")
        self.assertEqual(access.token, self.token.key)

    def test_resource_matches_the_configured_resource_server(self):
        # AuthSettings.validate_token_resource=True makes the bearer middleware
        # compare these two. If this drifts, every request 401s.
        self.assertEqual(self.verify(self.token.key).resource, f"{PUBLIC_URL}/mcp")

    def test_unknown_token_is_refused(self):
        self.assertIsNone(self.verify("not-a-real-token"))

    def test_empty_token_is_refused(self):
        self.assertIsNone(self.verify(""))

    def test_deactivated_user_is_refused_though_the_token_row_survives(self):
        """
        A leaver keeps their token row; deactivating the account is how access
        is removed in this CRM. Without this check that token would go on
        working against the MCP endpoint after the person had left.

        status is the field to set, not is_active. User.save() derives is_active
        from status, so assigning is_active directly is silently reverted on the
        way to the database; this test failed for exactly that reason first
        time round.
        """
        self.user.status = User.Status.INACTIVE
        self.user.save(update_fields=["status"])
        self.assertIsNone(self.verify(self.token.key))

    def test_suspended_user_is_refused(self):
        self.user.status = User.Status.SUSPENDED
        self.user.save(update_fields=["status"])
        self.assertIsNone(self.verify(self.token.key))

    def test_stale_is_active_from_a_queryset_update_still_refuses(self):
        """
        A .update() writes the column without calling save(), so status and
        is_active can disagree. The verifier reads both and the refusal wins.
        """
        User.objects.filter(pk=self.user.pk).update(status=User.Status.INACTIVE)
        self.assertTrue(User.objects.get(pk=self.user.pk).is_active)
        self.assertIsNone(self.verify(self.token.key))
