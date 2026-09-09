"""
ASGI entry point.

WHY THIS EXISTS ALONGSIDE wsgi.py
The CRM itself is happy under WSGI and gunicorn, and nothing about the web app
needed changing. The MCP endpoint does. It is a long lived streaming connection
that a WSGI worker cannot hold open, so serving it means running the whole
project under ASGI. wsgi.py is left in place; whichever the process manager
points at is what runs.

    uvicorn config.asgi:application --host 0.0.0.0 --port 8000
    gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker

ROUTING
The dispatch below is by path prefix rather than through Django's URL conf, and
it has to be. Django resolves URLs inside its own handler, which is the thing we
are bypassing for /mcp; by the time urls.py could see the request the response
is already a Django response and cannot stream. It also means the React
catch-all in config/urls.py never gets the chance to swallow /mcp, which it
otherwise would, since its negative lookahead only exempts api/, admin/,
api-auth/ and static/.
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Django first, and before importing mcp_server. Loading the ORM is what makes
# DrfTokenVerifier importable, and get_asgi_application() is what sets the apps
# registry up. Importing the other order raises AppRegistryNotReady.
django_application = get_asgi_application()

from mcp_server import mcp  # noqa: E402

# streamable_http_path must match the prefix tested below, otherwise the inner
# Starlette app answers 404 to everything we hand it.
mcp_application = mcp.streamable_http_app(streamable_http_path="/mcp")


async def application(scope, receive, send):
    """
    Send /mcp to the MCP server and everything else to Django.

    Lifespan is routed to the MCP app as well as Django. The streamable HTTP
    transport starts its session manager in that event; without it every MCP
    request fails on a task group that was never entered.
    """
    path = scope.get("path", "")
    if scope["type"] == "lifespan":
        return await mcp_application(scope, receive, send)
    if path == "/mcp" or path.startswith("/mcp/"):
        return await mcp_application(scope, receive, send)
    return await django_application(scope, receive, send)
