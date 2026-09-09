"""
LINQ CRM MCP server, stdio transport.

Wraps the Data API that already exists at /api/data/, so Claude reads bookings,
delegates, events and tickets through the same X-DATA-API-KEY credential the
Sheets sync uses. Nothing here can write. The surface it calls is a
ReadOnlyModelViewSet and the key never authenticates as a CRM user, see
dataapi/authentication.py.

Setup, three steps.
  1. python manage.py create_data_api_key "Claude MCP"
  2. pip install mcp
  3. set LINQ_DATA_API_KEY to the printed dapi_ key, then point Claude at
     this file; .mcp.json in the repo root already does that for Claude Code.

Check it works, LINQ_DATA_API_KEY=dapi_... python backend/mcp_server.py --selftest
"""
import os
import sys
from urllib.parse import parse_qs, urlparse

import requests
from mcp.server.mcpserver import MCPServer
# ToolError is the only exception whose text reaches the model. Anything else
# is masked as a bare Error executing tool, which tells Claude nothing about a
# scope refusal or a bad watermark, so every failure below raises this one.
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

BASE_URL = os.environ.get("LINQ_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("LINQ_DATA_API_KEY", "")
# Mirrors dataapi.models.DATA_API_SCOPES. A key may be scoped narrower than
# this, in which case the server answers 403 and _get passes that body through.
RESOURCES = ("bookings", "delegates", "events", "tickets")
TIMEOUT = 30

mcp = MCPServer("linq-crm")
# Every tool here is a GET against a ReadOnlyModelViewSet, so say so and let
# clients skip the write confirmation.
READ_ONLY = ToolAnnotations(readOnlyHint=True)


def _params(event_code="", status="", updated_since="", page_size=None, cursor=""):
    """Drop the unset filters, the Data API ignores an empty value anyway."""
    pairs = {
        "event_code": event_code,
        "status": status,
        "updated_since": updated_since,
        "page_size": page_size,
        "cursor": cursor,
    }
    return {k: v for k, v in pairs.items() if v not in (None, "")}


def _cursor_of(next_url):
    """Pull the opaque cursor out of the next URL, so no URL crosses the tool boundary."""
    if not next_url:
        return ""
    return parse_qs(urlparse(next_url).query).get("cursor", [""])[0]


def _check(resource):
    if resource not in RESOURCES:
        raise ToolError(f"Unknown resource {resource!r}, one of {', '.join(RESOURCES)}.")


def _get(path, params=None):
    if not API_KEY:
        raise ToolError("LINQ_DATA_API_KEY is not set, create one with "
                        "manage.py create_data_api_key.")
    try:
        resp = requests.get(
            f"{BASE_URL}/api/data/{path}",
            params=params or {},
            headers={"X-DATA-API-KEY": API_KEY},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        # A stopped dev server is the everyday failure here. Named, because
        # otherwise it reaches Claude as an unexplained crash and it retries.
        raise ToolError(f"Cannot reach the CRM at {BASE_URL}, {exc}") from exc
    if resp.status_code >= 400:
        # Scope refusals and bad watermarks are explained in the body, so pass
        # it through rather than a bare status Claude cannot act on.
        raise ToolError(f"{resp.status_code} from {resp.url}, {resp.text[:500]}")
    return resp.json()


@mcp.tool(annotations=READ_ONLY)
def list_records(resource: str, event_code: str = "", status: str = "",
                 updated_since: str = "", page_size: int = 50,
                 cursor: str = "") -> dict:
    """
    Read a page of CRM rows, newest ids last, ordered by primary key.

    resource is one of bookings, delegates, events, tickets.
    event_code filters bookings, delegates and tickets, for example MEC2026.
    status filters tickets only. updated_since takes an ISO 8601 timestamp.
    Feed next_cursor back in as cursor to read the following page; an empty
    next_cursor means that was the last one. The events catalogue takes no
    filters and returns every row.
    """
    _check(resource)
    data = _get(f"{resource}/",
                _params(event_code, status, updated_since, page_size, cursor))
    return {
        "results": data.get("results", []),
        "next_cursor": _cursor_of(data.get("next")),
    }


@mcp.tool(annotations=READ_ONLY)
def get_record(resource: str, record_id: int) -> dict:
    """Read one row by its numeric id. resource is as in list_records."""
    _check(resource)
    return _get(f"{resource}/{record_id}/")


@mcp.tool(annotations=READ_ONLY)
def count_records(resource: str, event_code: str = "", status: str = "",
                  updated_since: str = "") -> int:
    """
    Count the rows matching a filter without paging through them.

    Same resource and filters as list_records. Use this for how many questions,
    the list pages carry no total.
    """
    _check(resource)
    # ponytail: /ids/ ships every id and we keep only the count, roughly 350 KB
    # on the largest resource. Fine for a question asked now and then, add a
    # count-only endpoint if this gets called in a loop.
    return _get(f"{resource}/ids/", _params(event_code, status, updated_since))["count"]


def _selftest():
    assert _params() == {}
    assert _params(event_code="MEC2026", status="") == {"event_code": "MEC2026"}
    assert _params(page_size=50, cursor="cD0x") == {"page_size": 50, "cursor": "cD0x"}
    assert _cursor_of("") == ""
    assert _cursor_of("http://h/api/data/tickets/?cursor=cD0x&page_size=50") == "cD0x"
    try:
        _check("users")
    except ToolError as exc:
        # ToolError specifically, a ValueError here would reach Claude as an
        # unexplained crash instead of naming the four resources.
        assert "bookings" in str(exc), exc
    else:
        raise AssertionError("_check accepted a resource that is not a scope")
    if API_KEY:
        page = _get("events/", {"page_size": 1})
        assert page["resource"] == "events", page
        print(f"live ok, {len(page['results'])} event row from {BASE_URL}")
    else:
        print("no LINQ_DATA_API_KEY, skipped the live call")
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        mcp.run()
