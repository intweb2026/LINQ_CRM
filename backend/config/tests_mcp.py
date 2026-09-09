"""
Tests for the MCP server's mounting and its path handling.

The OAuth flow itself lives in mcp_auth. What is checked here is the seam
between Django and the MCP app, which is exactly where a mistake is silent:
a route the ASGI router forgets to forward is answered by the React catch-all
with a 200, and nothing looks broken until a client tries to use it.
"""
from django.test import SimpleTestCase

from mcp.server.mcpserver.exceptions import ToolError

import mcp_server


class AsgiRoutingTests(SimpleTestCase):
    """
    The ASGI router has to forward every path the MCP app owns.

    This failed in production once. The OAuth discovery document fell through
    to Django, where the React catch-all answered it with the frontend and a
    200, so nothing looked wrong until a client tried to authenticate.
    """

    def test_every_mcp_route_is_forwarded(self):
        from config.asgi import MCP_PATHS, mcp_application

        owned = {r.path for r in mcp_application.routes if hasattr(r, "path")}
        self.assertTrue(owned, "the MCP app exposed no routes at all")
        self.assertEqual(owned - MCP_PATHS, set(),
                         "these MCP routes would fall through to Django")

    def test_the_oauth_endpoints_are_actually_mounted(self):
        """
        Naming them explicitly, not just deriving the set above.

        The derived check passes happily if the SDK mounts nothing at all,
        which is what would happen if auth_server_provider were dropped from
        the server. These four are what the connector dialog needs to exist.
        """
        from config.asgi import MCP_PATHS

        for path in ("/authorize", "/token", "/register", "/revoke",
                     "/.well-known/oauth-authorization-server"):
            self.assertIn(path, MCP_PATHS)


class FrontendProxyTests(SimpleTestCase):
    """
    The production frontend forwards a fixed list of prefixes to Django and
    answers everything else with the React shell. A route the MCP app serves
    but that list omits is unreachable in production and perfectly fine in
    every test and local run, which is the worst combination.
    """

    # Mirrors shouldProxy() in serve-build.mjs, which matches on equality or
    # prefix. Restating the rule rather than looking for literals, because
    # /.well-known/oauth-protected-resource/mcp is forwarded by the prefix
    # above it and never appears in that file by name.
    @staticmethod
    def _prefixes():
        import re
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2]
                  / "frontend" / "scripts" / "serve-build.mjs").read_text(encoding="utf-8")
        block = re.search(r"const PROXY_PREFIXES = \[(.*?)\];", source, re.S)
        assert block, "PROXY_PREFIXES is no longer an array literal in serve-build.mjs"
        return re.findall(r"'([^']+)'", block.group(1))

    def test_the_frontend_forwards_every_mcp_route(self):
        from config.asgi import MCP_PATHS

        prefixes = self._prefixes()
        self.assertTrue(prefixes, "read no prefixes at all")
        missing = [
            path for path in MCP_PATHS
            if not any(path == p or path.startswith(p) for p in prefixes)
        ]
        self.assertEqual(missing, [], "add these to PROXY_PREFIXES in serve-build.mjs")

    def test_the_api_prefix_is_still_forwarded(self):
        # The consent page lives under /api/mcp-auth/, and the whole web app
        # depends on this one. A tidy-up that dropped it would take the CRM
        # down, so it is asserted rather than assumed.
        self.assertIn("/api/", self._prefixes())


class UrlNormalisationTests(SimpleTestCase):
    def test_a_path_is_absolute_and_slash_terminated(self):
        base = mcp_server.BASE_URL
        self.assertEqual(mcp_server._url("tickets"), f"{base}/api/tickets/")
        self.assertEqual(mcp_server._url("/api/tickets/"), f"{base}/api/tickets/")
        self.assertEqual(mcp_server._url("api/tickets/296410"),
                         f"{base}/api/tickets/296410/")
        self.assertEqual(mcp_server._url(""), f"{base}/api/")

    def test_a_query_string_in_the_path_is_refused(self):
        # It would survive the trailing-slash rule and then be sent as part of
        # the path, so the filter would silently do nothing.
        with self.assertRaises(ToolError):
            mcp_server._url("tickets/?page=2")

    def test_the_internal_url_is_not_the_public_one(self):
        # Defaulting to the public address would let a local run drive
        # production. See the comment on MCP_INTERNAL_API_URL in settings.
        self.assertNotEqual(mcp_server.BASE_URL, mcp_server.PUBLIC_URL)


class WriteGuardTests(SimpleTestCase):
    def test_a_read_method_is_refused_and_says_where_to_go(self):
        with self.assertRaises(ToolError) as caught:
            mcp_server.api_write("GET", "tickets/")
        self.assertIn("api_get", str(caught.exception))

    def test_an_unauthenticated_call_never_reaches_the_network(self):
        # There is no process-wide credential any more. Outside a request
        # get_access_token() returns None, and the tool must refuse rather
        # than call the CRM anonymously.
        with self.assertRaises(ToolError) as caught:
            mcp_server.api_get("tickets/")
        self.assertIn("credential", str(caught.exception))
