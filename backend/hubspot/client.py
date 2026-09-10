"""
hubspot/client.py
──────────────────
One HTTP client for HubSpot, shared by every module that needs it.

WHY THIS IS ITS OWN APP AND NOT PART OF credit_control. Two modules need
HubSpot: Credit Control wants phones, per-contact call counts and email
evidence, and Ticket Central wants mailable contact counts per event code.
A client living inside one of them would be imported by the other, which is the
shape that ends with two half-copies drifting apart.

THREE RULES THIS FILE ENFORCES.

  1. NOTHING HERE IS CALLED FROM A WEB REQUEST. Every function is for a cron
     job or a management command, which writes into the cache tables in
     models.py; views read those tables. HubSpot is a third party with its own
     rate limits and its own bad days, and a page that calls it inline becomes a
     page that hangs when HubSpot is slow and shows nothing when the token is
     wrong. Cached, the worst case is a figure a few hours old with the time it
     was fetched printed beside it.

  2. A MISSING TOKEN IS NOT AN ERROR. `enabled()` is false, callers skip their
     work and log once. The CRM has to keep working on a machine that has no
     HubSpot credential, which includes every developer's laptop and CI.

  3. TIMESTAMPS ARE UTC, ALWAYS. HubSpot returns ISO-8601 UTC (and epoch
     milliseconds on some properties) regardless of what timezone the portal is
     configured to *display*. The trap is reading a time off the HubSpot screen
     and treating it as the stored value. `parse_ts` is the only place a
     HubSpot timestamp becomes a datetime, and it always produces an aware UTC
     one.

AUTH is a private app access token, sent as `Authorization: Bearer <token>`.
Not an API key: HubSpot sunset those in 2022. Not OAuth either, which only earns
its refresh-token machinery when you distribute an app to portals you do not
own. Scopes needed on the private app, all read-only:

    crm.objects.contacts.read   phones, contact ids, marketable status
    crm.objects.calls.read      call counts and the per-shift matrix
    crm.objects.emails.read     the evidence the invoice-type classifier reads
    crm.objects.owners.read     owner id -> CRM user, so no hard-coded ids

A scope you forgot to tick fails as a 403 on that one call, not at startup, so
`HubSpotError` carries the status and the caller decides whether to degrade or
stop.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone as dt_timezone

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BASE = "https://api.hubapi.com"

# Endpoints, spelled once. `search` is the workhorse: it returns a `total`
# alongside the page, which is how a count of matching contacts costs one
# request instead of paging the whole set.
CONTACTS_SEARCH = f"{BASE}/crm/v3/objects/contacts/search"
CONTACTS_BATCH_READ = f"{BASE}/crm/v3/objects/contacts/batch/read"
CALLS_SEARCH = f"{BASE}/crm/v3/objects/calls/search"
EMAILS_BATCH_READ = f"{BASE}/crm/v3/objects/emails/batch/read"
OWNERS = f"{BASE}/crm/v3/owners"


def _assoc_url(object_type: str, object_id: str, to_type: str) -> str:
    return f"{BASE}/crm/v4/objects/{object_type}/{object_id}/associations/{to_type}"


# HubSpot caps a batch read at 100 inputs and a search page at 200 results.
# These are the API's numbers, not a tuning choice, so they live here as names
# rather than as literals sprinkled through the callers.
BATCH_LIMIT = 100
SEARCH_PAGE_LIMIT = 200

# A search cannot page beyond 10,000 results. Anything that could exceed it has
# to be sliced by a filter (a date window, an owner) rather than paged further,
# and a caller that hits this is telling us its filter is too wide.
SEARCH_RESULT_CEILING = 10_000

# Politeness, not a limit. Private-app tokens sit somewhere around 100-190
# requests per 10 seconds depending on the HubSpot tier, with the search
# endpoints capped tighter, so a small gap between calls keeps a bulk job well
# clear of a 429 without any bookkeeping. A 429 is still handled below, because
# another process may be spending the same budget.
_MIN_INTERVAL = 0.12
_last_call = 0.0


class HubSpotError(RuntimeError):
    """A HubSpot call that failed. `status` is the HTTP status, or None."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def token() -> str:
    return (getattr(settings, "HUBSPOT_TOKEN", "") or "").strip()


def enabled() -> bool:
    """False on a machine with no HubSpot credential. Callers skip, not crash."""
    return bool(token())


def parse_ts(value) -> datetime | None:
    """
    A HubSpot timestamp as an aware UTC datetime.

    Accepts what the API actually returns, which is either an ISO-8601 string
    ending in Z or epoch milliseconds, sometimes as a numeric string. Returns
    None for anything unparseable rather than raising, because one malformed
    timestamp in a page of a hundred must not lose the other ninety-nine.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=dt_timezone.utc)
    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text) / 1000, tz=dt_timezone.utc)
    try:
        # fromisoformat handles the offset forms; Z is not one of them before
        # 3.11, and this project targets newer, but the swap costs nothing.
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(dt_timezone.utc)
    except ValueError:
        logger.debug("hubspot: unparseable timestamp %r", value)
        return None


def to_millis(when: datetime) -> int:
    """A datetime as the epoch milliseconds HubSpot's date filters compare on."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt_timezone.utc)
    return int(when.timestamp() * 1000)


def _request(method: str, url: str, *, json=None, params=None, timeout=30, _retries=2):
    """
    One HubSpot call, throttled, retried on 429 and 5xx, raising HubSpotError.

    The retry honours `Retry-After` when HubSpot sends it. Retrying on a fixed
    sleep instead is what turns a rate limit into a slower rate limit; the
    header is HubSpot telling us exactly how long to wait.
    """
    global _last_call
    if not enabled():
        raise HubSpotError("HUBSPOT_TOKEN is not set")

    gap = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if gap > 0:
        time.sleep(gap)

    headers = {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.request(
            method, url, headers=headers, json=json, params=params, timeout=timeout,
        )
    except requests.RequestException as exc:
        if _retries > 0:
            time.sleep(1.5)
            return _request(method, url, json=json, params=params,
                            timeout=timeout, _retries=_retries - 1)
        raise HubSpotError(f"HubSpot unreachable: {exc}") from exc
    finally:
        _last_call = time.monotonic()

    if response.status_code == 429 or response.status_code >= 500:
        if _retries > 0:
            wait = response.headers.get("Retry-After")
            time.sleep(float(wait) if wait else 2.0)
            return _request(method, url, json=json, params=params,
                            timeout=timeout, _retries=_retries - 1)

    if not response.ok:
        # The body carries HubSpot's own message, which names the missing scope
        # on a 403. Truncated because it can be long and it lands in a log.
        raise HubSpotError(
            f"HubSpot {response.status_code} on {url}: {response.text[:300]}",
            status=response.status_code,
        )
    if not response.content:
        return {}
    return response.json()


# ── Contacts ────────────────────────────────────────────────────────────────

# The contact properties Credit Control displays. `phone` and `mobilephone` are
# HubSpot's own internal names; `direct_phone_number` is a custom property on
# this portal. Asking for a property that does not exist is a 400 on the whole
# call, so anything added here has to exist in the portal first.
CONTACT_PROPS = ("email", "phone", "mobilephone", "direct_phone_number")

# The other places an address can live on a HubSpot contact.
#
# A batch read keyed on `email` matches the PRIMARY address only, so a delegate
# whose booking used their secondary address came back as "no contact in
# HubSpot" and the invoice-type classifier then read that absence as evidence of
# a blind invoice. That is a wrong verdict produced by a lookup miss, which is
# the worst kind, so the miss is retried across these.
#
# `hs_additional_emails` is multi-valued and is matched with CONTAINS_TOKEN;
# the rest are ordinary strings matched with IN.
SECONDARY_EMAIL_PROPS = ("email_2", "work_email", "other_email")
MULTI_EMAIL_PROP = "hs_additional_emails"
ALL_EMAIL_PROPS = ("email", MULTI_EMAIL_PROP) + SECONDARY_EMAIL_PROPS


def contacts_by_email(emails: list[str], props=CONTACT_PROPS) -> dict[str, dict]:
    """
    Look up contacts by email address, a hundred at a time.

    Keyed on the LOWERCASED email, because HubSpot matches an email case
    insensitively but echoes back whatever case it stored, and a caller that
    looks up what it asked for would miss half the answers.

    A batch read with `idProperty: "email"` returns 207 with per-input errors
    when some emails are unknown, which is the normal case here and not a
    failure: an unknown email simply does not appear in the result.
    """
    found: dict[str, dict] = {}
    clean = [e.strip() for e in emails if e and e.strip()]
    for start in range(0, len(clean), BATCH_LIMIT):
        chunk = clean[start:start + BATCH_LIMIT]
        payload = {
            "idProperty": "email",
            "properties": list(props),
            "inputs": [{"id": e} for e in chunk],
        }
        data = _request("POST", CONTACTS_BATCH_READ, json=payload)
        for row in data.get("results") or []:
            properties = row.get("properties") or {}
            email = (properties.get("email") or "").strip().lower()
            if email:
                found[email] = {"id": row.get("id"), **properties}
    return found


def contacts_by_any_email(emails: list[str], props=CONTACT_PROPS) -> dict[str, dict]:
    """
    Find contacts whose address matches on ANY email field, not just the primary.

    For the addresses a batch read could not resolve. One search per email
    property with an `IN` list, so it costs four requests for a hundred
    addresses rather than four per address.

    Keyed on the REQUESTED address rather than on the contact's primary, because
    the caller is holding a booking that used the secondary one and has nothing
    else to look the answer up by. A contact matching two requested addresses is
    returned under both, which is correct: they are the same person, and both
    bookings should find them.
    """
    found: dict[str, dict] = {}
    wanted = [e.strip().lower() for e in emails if e and e.strip()]
    if not wanted:
        return found

    # Every email field comes back, so the match can be resolved locally
    # without a second round trip per candidate.
    read_props = list(dict.fromkeys(tuple(props) + ALL_EMAIL_PROPS))

    for start in range(0, len(wanted), BATCH_LIMIT):
        chunk = wanted[start:start + BATCH_LIMIT]
        groups = [
            {"filters": [{"propertyName": prop, "operator": "IN", "values": chunk}]}
            for prop in ("email",) + SECONDARY_EMAIL_PROPS
        ]
        # CONTAINS_TOKEN takes one value, not a list, so the multi-valued field
        # gets a group per address. HubSpot allows up to five filterGroups, so
        # they are sent in their own batched requests below.
        payloads = [{"filterGroups": groups, "properties": read_props,
                     "limit": SEARCH_PAGE_LIMIT}]
        for token_chunk in [chunk[i:i + 3] for i in range(0, len(chunk), 3)]:
            payloads.append({
                "filterGroups": [
                    {"filters": [{"propertyName": MULTI_EMAIL_PROP,
                                  "operator": "CONTAINS_TOKEN", "value": one}]}
                    for one in token_chunk
                ],
                "properties": read_props,
                "limit": SEARCH_PAGE_LIMIT,
            })

        for payload in payloads:
            try:
                data = _request("POST", CONTACTS_SEARCH, json=payload)
            except HubSpotError as exc:
                logger.info("hubspot: secondary email search failed: %s", exc)
                continue
            for row in data.get("results") or []:
                properties = row.get("properties") or {}
                # Every address this contact holds, lowercased, so the requested
                # one can be recognised whichever field it arrived in.
                mine = set()
                for prop in ALL_EMAIL_PROPS:
                    raw = properties.get(prop) or ""
                    for piece in str(raw).replace(",", ";").split(";"):
                        piece = piece.strip().lower()
                        if piece:
                            mine.add(piece)
                for asked in chunk:
                    if asked in mine and asked not in found:
                        found[asked] = {"id": row.get("id"), **properties}
    return found


def count_contacts(filters: list[dict]) -> int:
    """
    How many contacts match, without fetching them.

    The search response carries `total` next to the page, so asking for one
    result and reading the total answers a count in a single request whatever
    the count is. This is what Ticket Central's mailable-count-per-event-code
    will be built on.

    `filters` is one filterGroup's worth of filter dicts, ANDed together, in
    HubSpot's own shape, e.g.
        [{"propertyName": "contact_purpose", "operator": "EQ", "value": "ISCC"}]
    """
    payload = {"filterGroups": [{"filters": filters}], "limit": 1, "properties": ["email"]}
    data = _request("POST", CONTACTS_SEARCH, json=payload)
    return int(data.get("total") or 0)


# ── Calls ───────────────────────────────────────────────────────────────────

def calls_since(since: datetime, page_cap: int = 40):
    """
    Yield call engagements logged at or after `since`, newest pages first.

    Incremental by design. The caller keeps the highest `hs_timestamp` it has
    seen and passes it back next run, so a nightly job fetches a night's calls
    rather than re-reading the window every time. `page_cap` stops a runaway
    loop if a filter is wider than intended; hitting it is a signal, and it is
    logged.
    """
    after = None
    pages = 0
    while pages < page_cap:
        payload = {
            "filterGroups": [{"filters": [{
                "propertyName": "hs_timestamp",
                "operator": "GTE",
                "value": to_millis(since),
            }]}],
            "properties": ["hs_timestamp", "hubspot_owner_id", "hs_call_disposition"],
            "sorts": [{"propertyName": "hs_timestamp", "direction": "ASCENDING"}],
            "limit": SEARCH_PAGE_LIMIT,
        }
        if after:
            payload["after"] = after
        data = _request("POST", CALLS_SEARCH, json=payload)
        results = data.get("results") or []
        if not results:
            return
        yield from results
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        pages += 1
        if not after:
            return
    logger.warning("hubspot: calls_since hit the %s page cap, window may be too wide", page_cap)


def call_summary_for_contact(contact_id: str) -> dict | None:
    """
    How many calls this contact has had, when the last one was, and how long it
    ran. One request, not three.

    The calls search returns a `total` next to the page, so asking for the
    single newest row answers all three questions at once: the count comes from
    the total, and the last call and its duration come from that row. This is
    the only part of the sync that costs a request per contact, so it is worth
    not doing three times.

    Returns None when the lookup fails, so a caller can leave a good cached
    number alone rather than overwriting it with a zero. A DURATION OF ZERO IS
    NOT NOTHING: it is a call that connected to nobody, which is precisely what
    somebody about to dial again wants to know.
    """
    try:
        payload = {
            "filterGroups": [{"filters": [{
                "propertyName": "associations.contact",
                "operator": "EQ",
                "value": str(contact_id),
            }]}],
            "properties": ["hs_timestamp", "hs_call_duration", "hs_call_disposition"],
            "sorts": [{"propertyName": "hs_timestamp", "direction": "DESCENDING"}],
            "limit": 1,
        }
        data = _request("POST", CALLS_SEARCH, json=payload)
        total = data.get("total")
        if not isinstance(total, int):
            return None
        newest = (data.get("results") or [{}])[0].get("properties") or {}
        raw = newest.get("hs_call_duration")
        try:
            # HubSpot reports call duration in MILLISECONDS. Storing it raw and
            # calling it seconds is the classic way this column ends up
            # reporting three-hour phone calls.
            seconds = int(round(int(raw) / 1000)) if raw not in (None, "") else None
        except (TypeError, ValueError):
            seconds = None
        return {
            "total": total,
            "last_at": parse_ts(newest.get("hs_timestamp")),
            "last_seconds": seconds,
            "last_disposition": newest.get("hs_call_disposition") or "",
        }
    except HubSpotError as exc:
        if exc.status == 403:
            raise
        logger.debug("hubspot: call summary failed for contact %s: %s", contact_id, exc)
        return None


def count_mailable(purpose: str) -> dict:
    """
    Contacts under one `contact_purpose`, and how many are mailable.

    Two counts, two requests, no paging whatever the size: the search endpoint
    hands back a `total` and the page is asked to be one row wide.

    `mailable` is tested with HAS_PROPERTY rather than against a value. The
    property is an enumeration carrying a single option, so what is actually
    being asked is whether marketing has classified this contact at all, which
    is what "mailable is known" means.
    """
    base = [{"propertyName": "contact_purpose", "operator": "EQ", "value": purpose}]
    return {
        "total": count_contacts(base),
        "mailable": count_contacts(
            base + [{"propertyName": "mailable", "operator": "HAS_PROPERTY"}]
        ),
    }


# ── Emails, the classifier's evidence ───────────────────────────────────────

EMAIL_PROPS = (
    "hs_timestamp", "hs_email_subject", "hs_email_text",
    "hs_email_direction", "hs_email_status",
)


def email_ids_for_contact(contact_id: str, limit: int = 25) -> list[str]:
    """The ids of emails associated with a contact, newest-first not guaranteed."""
    try:
        data = _request("GET", _assoc_url("contacts", str(contact_id), "emails"),
                        params={"limit": limit})
    except HubSpotError as exc:
        logger.debug("hubspot: no emails for contact %s: %s", contact_id, exc)
        return []
    return [str(r.get("toObjectId")) for r in (data.get("results") or []) if r.get("toObjectId")]


def emails_by_id(email_ids: list[str], props=EMAIL_PROPS) -> list[dict]:
    """Read email engagements by id, a hundred at a time."""
    out: list[dict] = []
    for start in range(0, len(email_ids), BATCH_LIMIT):
        chunk = email_ids[start:start + BATCH_LIMIT]
        payload = {"properties": list(props), "inputs": [{"id": i} for i in chunk]}
        try:
            data = _request("POST", EMAILS_BATCH_READ, json=payload)
        except HubSpotError as exc:
            logger.debug("hubspot: email batch failed: %s", exc)
            continue
        for row in data.get("results") or []:
            out.append({"id": row.get("id"), **(row.get("properties") or {})})
    return out


# ── Owners ─────────────────────────────────────────────────────────────────

def owners_by_email() -> dict[str, str]:
    """
    Map lowercased owner email -> HubSpot owner id.

    Read live rather than stored in a constant. The GAS build carried a
    hard-coded rep-name-to-owner-id table, which is a second roster to keep in
    step with the team; matching on email means adding somebody to the Credit
    Control team is the only step.
    """
    mapping: dict[str, str] = {}
    after = None
    while True:
        params = {"limit": 100}
        if after:
            params["after"] = after
        data = _request("GET", OWNERS, params=params)
        for row in data.get("results") or []:
            email = (row.get("email") or "").strip().lower()
            if email and row.get("id"):
                mapping[email] = str(row["id"])
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            return mapping
