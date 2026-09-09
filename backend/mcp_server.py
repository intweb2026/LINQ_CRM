"""
LINQ CRM MCP server, stdio transport.

COMPLETE APP ACCESS. This authenticates with a DRF token belonging to a real
CRM account, so it reaches every /api/ endpoint that account is allowed to
reach and it can write. A token for an admin is superuser equivalent; anything
you can do in the CRM UI, Claude can do here, deletes included. Scope it by
minting the token for an account with the role you actually want, not by
trusting this file.

Three generic tools rather than one per resource, because the API is a DRF
router; the endpoint list is discoverable at runtime and would only rot if
copied here. See api_endpoints.

Setup, three steps.
  1. python manage.py drf_create_token HP
  2. pip install mcp
  3. put the printed token in LINQ_CRM_TOKEN, see .mcp.json in the repo root.

Revoke, python manage.py shell -c "from rest_framework.authtoken.models import
Token; Token.objects.filter(user__username='HP').delete()"

Check it works, LINQ_CRM_TOKEN=... python backend/mcp_server.py --selftest
"""
import os
import sys

import requests
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
# ToolError is the only exception whose text reaches the model. Anything else
# is masked as a bare Error executing tool, which tells Claude nothing about a
# 403 or a validation error, so every failure below raises this one.
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

BASE_URL = os.environ.get("LINQ_API_URL", "http://localhost:8000").rstrip("/")
# Used by the stdio transport only. Over HTTP the credential belongs to the
# caller, not to the process, and _token() prefers that one. See _token.
ENV_TOKEN = os.environ.get("LINQ_CRM_TOKEN", "")
# The public identity of this endpoint. MCP clients fetch OAuth metadata built
# from it, so it has to be the address they reached us on, not an internal one.
PUBLIC_URL = os.environ.get("LINQ_MCP_PUBLIC_URL", "https://www.app.iq-hub.com").rstrip("/")
WRITE_METHODS = ("POST", "PATCH", "PUT", "DELETE")
TIMEOUT = 60

class DrfTokenVerifier:
    """
    Verify a bearer token against the CRM's own authtoken table.

    This is the seam OAuth plugs into later. Today a caller presents the DRF
    token their account already has, and the tools then act as that account, so
    RBAC is enforced by DRF exactly as it is for the web app; there is no second
    authorisation model to keep in step. Replacing this class with an OAuth
    verifier changes how the caller proves who they are and nothing else.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        # Imported here, not at module scope. This file is also the stdio entry
        # point, which runs with no Django settings configured, and importing a
        # model at import time would fail there before anything else ran.
        from asgiref.sync import sync_to_async
        from rest_framework.authtoken.models import Token

        @sync_to_async
        def lookup():
            try:
                row = Token.objects.select_related("user").get(key=token)
            except Token.DoesNotExist:
                return None
            # A leaver keeps their token row, so the account state is what has
            # to be checked, not the row's existence.
            #
            # BOTH fields, deliberately. status is the source of truth in this
            # CRM and User.save() derives is_active from it, see
            # accounts/models.py; but a queryset .update() writes the column
            # without calling save(), and the two would then disagree. Reading
            # both means whichever one says no wins.
            user = row.user
            if not user.is_active or user.status != user.Status.ACTIVE:
                return None
            return user.username

        username = await lookup()
        if username is None:
            return None
        return AccessToken(
            token=token,
            client_id=username,
            scopes=["crm"],
            subject=username,
            resource=f"{PUBLIC_URL}/mcp",
        )


def _auth_header():
    """
    The complete Authorization header to call the CRM with.

    THE SCHEME IS PART OF THE ANSWER, not a detail the caller can assume. Two
    different credentials reach this function and the CRM authenticates them
    with two different classes.

      Bearer  an MCP access token, minted by the OAuth flow and belonging to
              the person on the other end. Verified by
              mcp_auth.authentication.McpTokenAuthentication.
      Token   a DRF token from the environment, used only by the stdio
              transport where there is no caller to belong to. Verified by
              DRF's own TokenAuthentication.

    Sending one under the other's scheme is a 401, because neither lookup
    table contains the other's values. get_access_token() returns None outside
    a request, which is how the two cases are told apart.
    """
    access = get_access_token()
    if access is not None:
        return f"Bearer {access.token}"
    if ENV_TOKEN:
        return f"Token {ENV_TOKEN}"
    return ""


mcp = MCPServer(
    "linq-crm",
    token_verifier=DrfTokenVerifier(),
    auth=AuthSettings(
        issuer_url=PUBLIC_URL,
        resource_server_url=f"{PUBLIC_URL}/mcp",
        # DrfTokenVerifier stamps resource with this same value, so the bearer
        # middleware can enforce it. Left unset the check is skipped, and a
        # token minted for some other audience would be accepted here.
        validate_token_resource=True,
    ),
)
READ_ONLY = ToolAnnotations(readOnlyHint=True)
# Writes go to the live CRM under a real account. destructiveHint is what makes
# a client prompt before each one, which is the only gate between Claude and a
# DELETE, so it is load bearing rather than decoration.
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


def _url(path):
    """
    Normalise a caller supplied path to an absolute CRM URL.

    The trailing slash is enforced rather than left to APPEND_SLASH. Django
    answers a slashless POST with a 301 to the slashed path, requests follows
    it, and the redirect turns the POST into a GET, so a write would silently
    do nothing. Accepts tickets/, /api/tickets/ and api/tickets/ alike.
    """
    clean = str(path).strip().lstrip("/")
    if clean.startswith("api/"):
        clean = clean[len("api/"):]
    if "?" in clean:
        raise ToolError("Put query parameters in params, not in path.")
    if clean and not clean.endswith("/"):
        clean += "/"
    return f"{BASE_URL}/api/{clean}"


def _call(method, path, params=None, body=None):
    authorization = _auth_header()
    if not authorization:
        raise ToolError("No CRM credential. Over stdio set LINQ_CRM_TOKEN, mint "
                        "one with manage.py drf_create_token <username>.")
    try:
        resp = requests.request(
            method,
            _url(path),
            params=params or {},
            json=body,
            headers={"Authorization": authorization},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        # A stopped dev server is the everyday failure here. Named, because
        # otherwise it reaches Claude as an unexplained crash and it retries.
        raise ToolError(f"Cannot reach the CRM at {BASE_URL}, {exc}") from exc

    if resp.status_code >= 400:
        # DRF explains 403s and field validation in the body, so pass it
        # through rather than a bare status Claude cannot act on.
        raise ToolError(f"{resp.status_code} {method} {resp.url}, {resp.text[:1000]}")
    if resp.status_code == 204 or not resp.content:
        # 204 on DELETE, and any empty 200. json() would raise on both.
        return {"status": resp.status_code, "detail": "No content."}
    try:
        return resp.json()
    except ValueError:
        return {"status": resp.status_code, "text": resp.text[:2000]}


@mcp.tool(annotations=READ_ONLY)
def api_endpoints() -> dict:
    """
    List the CRM API endpoints available to this account.

    Read this first when you do not already know the path for something. It is
    the live DRF router index, so it never disagrees with the running server.
    Endpoints outside the router are not listed; those include reports/,
    performance-matrix/, mining-matrix/, historical-events/, pre-event-docs/,
    google-sync/, search/ and stats/dashboard/.
    """
    return _call("GET", "")


@mcp.tool(annotations=READ_ONLY)
def api_get(path: str, params: dict | None = None) -> dict:
    """
    Read any CRM endpoint.

    path is relative, for example tickets/, tickets/296410/ or
    stats/dashboard/. A leading /api/ is optional.

    params carries the query string. Lists are page numbered, so page and
    page_size up to 1000 walk them, and the reply carries count and
    total_pages. Most lists also take search= for free text and ordering= for
    sort. Ask for the smallest page that answers the question; a 1000 row page
    of some resources is hundreds of kilobytes.
    """
    return _call("GET", path, params=params)


@mcp.tool(annotations=WRITES)
def api_write(method: str, path: str, body: dict | None = None) -> dict:
    """
    Create, update or delete through the CRM API. This changes live data.

    method is POST to create, PATCH to change some fields, PUT to replace, or
    DELETE to remove. path is as in api_get; a write to one row needs its id,
    for example PATCH tickets/296410/. body is the JSON payload, omitted for
    DELETE.

    Read the row with api_get before changing it, and prefer PATCH to PUT so
    fields you did not mention keep their values.
    """
    verb = str(method).strip().upper()
    if verb not in WRITE_METHODS:
        raise ToolError(f"method must be one of {', '.join(WRITE_METHODS)}, "
                        f"got {method!r}. Use api_get to read.")
    return _call(verb, path, body=body)


def _selftest():
    assert _url("tickets") == f"{BASE_URL}/api/tickets/"
    assert _url("/api/tickets/") == f"{BASE_URL}/api/tickets/"
    assert _url("api/tickets/296410") == f"{BASE_URL}/api/tickets/296410/"
    assert _url("") == f"{BASE_URL}/api/"
    try:
        _url("tickets/?page=2")
    except ToolError:
        pass
    else:
        raise AssertionError("_url accepted a query string in the path")
    try:
        api_write("GET", "tickets/")
    except ToolError as exc:
        # ToolError specifically, a ValueError here would reach Claude as an
        # unexplained crash instead of telling it to use api_get.
        assert "api_get" in str(exc), exc
    else:
        raise AssertionError("api_write accepted GET")

    if ENV_TOKEN:
        roots = _call("GET", "")
        print(f"live ok, {len(roots)} router endpoints, {', '.join(sorted(roots))}")
        page = _call("GET", "events/", params={"page_size": 1})
        print(f"events count {page['count']}")
    else:
        print("no LINQ_CRM_TOKEN, skipped the live calls")
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        mcp.run()
