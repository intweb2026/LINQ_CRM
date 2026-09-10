"""
The MCP server, mounted at /mcp by config/asgi.py.

WHAT THIS IS
A remote MCP endpoint for the CRM, the kind claude.ai adds as a custom
connector. A person authorises it with their Google account, the tools then act
as that person, and DRF enforces their CRM permissions exactly as it does for
the web app. There is no shared service credential anywhere in the flow.

WHAT IT IS NOT, ANY MORE
This used to double as a stdio server for Claude Code on a developer's laptop,
carrying a DRF token from the environment. That is gone. It cost a conditional
import, an env-token fallback and a second credential path, all to support a
mode nobody wanted; and the OAuth provider imports Django models, which a stdio
process cannot do. One purpose, less code. Restoring it means reverting the
commit that removed .mcp.json alongside this.

THE SDK OWNS THE PROTOCOL. Passing auth_server_provider mounts the OAuth
endpoints, /authorize, /token, /register, /revoke and the authorization server
metadata, on the same Starlette app as /mcp. config/asgi.py forwards whatever
routes that app declares, so nothing here needs listing them; but the
production frontend proxy does, see frontend/scripts/serve-build.mjs.

WHY THE TOOLS CALL THE CRM OVER HTTP rather than reaching into the ORM. One
code path serves every endpoint, and every permission class, RBAC scope and
audit line behaves as though the person made the request themselves. Bypassing
the API would mean reimplementing all of that here, and getting it subtly
wrong. See mcp_auth/authentication.py for how the bearer token is accepted at
the other end.
"""
from urllib.parse import urlparse

from django.conf import settings
import requests
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
# ToolError is the only exception whose text reaches the model. Anything else
# is masked as a bare Error executing tool, which tells Claude nothing about a
# 403 or a validation error, so every failure below raises this one.
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from mcp_auth.provider import DjangoOAuthProvider

PUBLIC_URL = settings.MCP_PUBLIC_URL
# Where the tools reach the CRM's own API.
#
# LOOPBACK BY DEFAULT, not the public URL, so a developer running this locally
# cannot accidentally drive production. PORT is what the platform sets, 3000 on
# Coolify, and 8000 is the Django development default.
#
# THE HOST HEADER IS SENT SEPARATELY, below, because a loopback request carries
# "Host: 127.0.0.1" and Django answers 400 DisallowedHost unless that literal is
# in ALLOWED_HOSTS, which it is not in production. Sending the public host
# instead is the same thing the frontend proxy does.
BASE_URL = (
    settings.MCP_INTERNAL_API_URL
    or f"http://127.0.0.1:{settings.MCP_INTERNAL_PORT}"
).rstrip("/")
API_HOST = urlparse(PUBLIC_URL).netloc
WRITE_METHODS = ("POST", "PATCH", "PUT", "DELETE")
TIMEOUT = 60


def _transport_security():
    """
    Which Host headers the MCP transport will accept.

    THE DEFAULT IS 127.0.0.1 AND NOTHING ELSE. The SDK turns on DNS rebinding
    protection out of the box and, unconfigured, allows only the loopback name
    it was built with. Every real request therefore answers 421 Invalid Host
    header, and it does so AFTER the bearer check, which is why an anonymous
    probe still saw a tidy 401 and the fault only appeared once a token
    started working.

    Derived from the app's own configuration rather than restated, so it
    cannot drift: the public host the connector is reached on, plus whatever
    Django already trusts in ALLOWED_HOSTS. Each name is allowed with any port
    as well, because a proxy may forward one.

    ALLOWED_HOSTS of "*" means the deployment has deliberately stopped
    checking, so matching that here keeps one decision in one place instead of
    two that can disagree.
    """
    if "*" in settings.ALLOWED_HOSTS:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False,
                                         allowed_hosts=[], allowed_origins=[])

    names = {API_HOST, "127.0.0.1", "localhost"}
    names.update(h.lstrip(".") for h in settings.ALLOWED_HOSTS if h)
    names.discard("")
    hosts = sorted(names | {f"{n}:*" for n in names})
    origins = sorted(
        {f"{scheme}://{n}" for n in names for scheme in ("https", "http")}
        | {f"{scheme}://{n}:*" for n in names for scheme in ("https", "http")}
    )
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)


TRANSPORT_SECURITY = _transport_security()

mcp = MCPServer(
    "linq-crm",
    auth_server_provider=DjangoOAuthProvider(),
    auth=AuthSettings(
        issuer_url=PUBLIC_URL,
        resource_server_url=f"{PUBLIC_URL}/mcp",
        # DjangoOAuthProvider stamps resource with this same value. Left unset
        # the check is skipped, and a token minted for another audience would
        # be accepted here.
        validate_token_resource=True,
        # Dynamic client registration, and it is not optional. The connector
        # dialog takes a URL and nothing else, so a client that cannot register
        # itself can never be configured. Registration grants nothing on its
        # own; it only lets a client ASK, and every grant still requires a
        # person to sign in with Google and consent.
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=["crm"], default_scopes=["crm"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    ),
)
READ_ONLY = ToolAnnotations(readOnlyHint=True)
# Writes go to the live CRM as a real person. destructiveHint is what makes a
# client prompt before each one, which is the only gate between a bad tool call
# and a DELETE, so it is load bearing rather than decoration.
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


def _bearer():
    """
    The caller's own access token, or nothing.

    There is no process-wide credential to fall back on, deliberately. Every
    tool call belongs to the person whose consent produced the token, which is
    what makes the audit trail name real people.
    """
    access = get_access_token()
    return access.token if access is not None else ""


def _call(method, path, params=None, body=None):
    token = _bearer()
    if not token:
        # The transport should have refused an unauthenticated request long
        # before a tool ran, so this means the auth middleware was bypassed
        # rather than that somebody forgot to sign in.
        raise ToolError("This request carries no CRM credential.")
    try:
        resp = requests.request(
            method,
            _url(path),
            params=params or {},
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Host": API_HOST,
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
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
    List the CRM API endpoints available to you.

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
    Read any CRM endpoint, as the signed-in person.

    path is relative, for example tickets/, tickets/296410/ or
    stats/dashboard/. A leading /api/ is optional.

    params carries the query string. Lists are page numbered, so page and
    page_size up to 1000 walk them, and the reply carries count and
    total_pages. Most lists also take search= for free text and ordering= for
    sort. Ask for the smallest page that answers the question; a 1000 row page
    of some resources is hundreds of kilobytes.

    You see only what that person's CRM role allows. A 403 means their
    permissions do not cover it, not that the path is wrong.
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
    fields you did not mention keep their values. Every change is recorded
    against the signed-in person.
    """
    verb = str(method).strip().upper()
    if verb not in WRITE_METHODS:
        raise ToolError(f"method must be one of {', '.join(WRITE_METHODS)}, "
                        f"got {method!r}. Use api_get to read.")
    return _call(verb, path, body=body)
