"""
webhooks/utils.py
──────────────────
Shared helpers: IP extraction, header sanitisation, key validation.
"""
import json
from urllib.parse import parse_qsl, urlencode, urlparse

from django.conf import settings

# Names that carry the API key, in normalised form, meaning lowercased with
# hyphens folded to underscores. Matched against query parameter names and
# against header names alike.
#
# Deliberately narrow. Senders paste a URL that was generated for them rather
# than typing a parameter name from memory, so the generic spellings bought
# nothing; and "key", "apikey" and "api_key" were removed because they collide
# with a parameter a sender may already be carrying for a reason of its own. A
# collision is not a harmless miss, it is a 401 on a request that would
# otherwise have fallen through to the legacy secret and succeeded, because a
# non-empty value found here stops the search and becomes the credential.
#
# The zoho_* and zapikey entries are safe on exactly that test. They are
# specific vendor names rather than generic words, so nothing is carrying one
# for an unrelated purpose, and a request that sends one is a request that
# means it as a credential. The event websites hold their key in a field named
# for that vendor, and we do not yet know whether they transmit it as a header
# or as a query parameter, so both carriers accept every one of these.
QUERY_KEY_ALIASES = frozenset({
    "x_crm_api_key",
    "crm_api_key",
    "crm_key",
    "crmkey",
    "zoho_flow_key",
    "zapikey",
    "zoho_key",
})

# What a redacted key value is replaced with. Deliberately URL-safe so a scrubbed
# Referer is still a readable URL.
REDACTED = "REDACTED"

# Every key this CRM issues begins with this. It is what lets a value be
# recognised as a credential without knowing the name it arrived under, which is
# the only defence available against a carrier nobody anticipated.
KEY_VALUE_PREFIX = "crm_live_"


def _normalise_param(name) -> str:
    """Fold a parameter name to the form used in QUERY_KEY_ALIASES."""
    return str(name or "").strip().lower().replace("-", "_")


def _query_items(request) -> list:
    """
    (name, value) pairs from the query string.

    Works with a DRF Request (`query_params`) and a plain Django HttpRequest
    (`GET`) alike, and returns an empty list when the object has neither, since
    this is called from validation helpers that are also handed bare objects in
    tests.
    """
    params = getattr(request, "query_params", None)
    if params is None:
        params = getattr(request, "GET", None)
    if params is None:
        return []
    try:
        return list(params.items())
    except Exception:
        return []


def looks_like_a_key(value) -> bool:
    """
    True when a value has the shape of a key this CRM issued.

    Name-based rules can only protect the carriers someone thought of. This
    recognises the credential itself, so a key arriving under a header nobody
    anticipated is still handled as a secret.
    """
    return isinstance(value, str) and value.startswith(KEY_VALUE_PREFIX)


def _find_key(request):
    """
    (value, transport, carrier_name) for the key on this request.

    The precedence below IS the compatibility guarantee, in this order and no
    other.

    1. HTTP_X_CRM_API_KEY exactly. Every sender integrated to date uses this,
       so it is resolved before anything else can be consulted and no existing
       integration's behaviour can be altered by this function.
    2. Any other header whose name matches the alias set. Django presents a
       header as HTTP_ plus the uppercased name with hyphens as underscores, so
       a header named ZOHO-FLOW-KEY arrives as HTTP_ZOHO_FLOW_KEY; the prefix
       comes off and the remainder is normalised the same way a query parameter
       name is. Iterated in sorted name order, so a request carrying two alias
       headers resolves to the same one on every run rather than to whichever
       the environ happened to yield first.
    3. The query string, same alias set.

    A wrong key in a header is never quietly rescued by a right key further
    down the order; the first non-empty value found is the credential, and it
    either works or it does not.
    """
    canonical = str(request.META.get("HTTP_X_CRM_API_KEY") or "").strip()
    if canonical:
        return canonical, "header", "X_CRM_API_KEY"

    for meta_name in sorted(request.META):
        if not meta_name.startswith("HTTP_") or meta_name == "HTTP_X_CRM_API_KEY":
            continue
        bare = meta_name[len("HTTP_"):]
        if _normalise_param(bare) in QUERY_KEY_ALIASES:
            header_value = str(request.META.get(meta_name) or "").strip()
            if header_value:
                return header_value, "header", bare

    for name, value in _query_items(request):
        if _normalise_param(name) in QUERY_KEY_ALIASES:
            query_value = str(value or "").strip()
            if query_value:
                return query_value, "query", name

    return "", "", ""


def extract_api_key(request):
    """
    Find the API key on the request.

    Returns (key_value, transport) where transport is "header", "query", or ""
    when no key was sent at all. Deliberately a two-tuple; the carrier name is
    available separately from key_carrier_name() so that adding it cost no call
    site a signature change.
    """
    value, transport, _carrier = _find_key(request)
    return value, transport


def key_transport(request) -> str:
    """
    Which carrier the key arrived on, one of "header", "query", or "".

    Exists so a view can stamp the audit trail without authenticate_request
    having to grow a third return value and break every call site for it.
    """
    return _find_key(request)[1]


def key_carrier_name(request) -> str:
    """
    The header or parameter name the key was actually found under, or "".

    Header names come back with the HTTP_ prefix removed. DIAGNOSTIC ONLY.
    Nothing authenticates on this, and nothing may start to; the name a sender
    chose says nothing about whether the value is a valid credential.
    """
    return _find_key(request)[2]


def scrub_key_from_url(value):
    """
    Replace the value of every key-carrying parameter in a URL or query string.

    Every other parameter is left in place with its own value. The input is
    returned unchanged when it carries nothing to redact, so this is safe to run
    over arbitrary header values.

    Non-alias values make a percent-encoding round trip through parse_qsl and
    urlencode, so a scrubbed URL is equivalent to the original rather than
    byte-identical to it. That is acceptable for an audit record, and it is the
    price of parsing the thing properly instead of by string surgery.
    """
    if not value:
        return value

    text = str(value)
    head, sep, query = text.partition("?")
    target = query if sep else text
    if not target:
        return value

    try:
        items = parse_qsl(target, keep_blank_values=True)
    except (ValueError, UnicodeDecodeError):
        return value

    if not any(_normalise_param(name) in QUERY_KEY_ALIASES for name, _ in items):
        return value

    scrubbed = urlencode([
        (name, REDACTED if _normalise_param(name) in QUERY_KEY_ALIASES else item_value)
        for name, item_value in items
    ])
    return f"{head}?{scrubbed}" if sep else scrubbed


def extract_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    val = forwarded or request.META.get("REMOTE_ADDR", "")
    return val if val else None


def safe_headers(meta: dict) -> dict:
    """
    HTTP_* headers from request.META, stripping all secret/key values.

    Redaction runs on two independent rules, because either one alone leaks.

    By NAME, from the alias set derived programmatically rather than retyped, so
    that adding a carrier to QUERY_KEY_ALIASES cannot leave this function behind
    holding a stale list. HTTP_X_WEBHOOK_SECRET and HTTP_X_API_KEY are named
    explicitly since neither is an alias and both are still secrets.

    By VALUE SHAPE, over every header that survives the name filter. The header
    NAME is exactly what an operator needs in order to learn how a given sender
    transmits its credential, so names are always kept in full; the VALUE is the
    thing that must never be stored. Redacting on shape means a key arriving
    under a name nobody anticipated is protected the moment it arrives, rather
    than after somebody notices it sitting in the logs.
    """
    skip = {"HTTP_" + alias.upper() for alias in QUERY_KEY_ALIASES}
    skip |= {"HTTP_X_WEBHOOK_SECRET", "HTTP_X_API_KEY"}
    headers = {
        k: v for k, v in meta.items()
        if k.startswith("HTTP_") and k not in skip
    }

    # A browser that follows a link to the ingest URL sends that whole URL back
    # as Referer, key and all, which would otherwise be written verbatim into
    # WebhookLog.headers and read by anyone with logs access.
    if "HTTP_REFERER" in headers:
        headers["HTTP_REFERER"] = scrub_key_from_url(headers["HTTP_REFERER"])

    for name, value in list(headers.items()):
        if looks_like_a_key(value):
            headers[name] = f"{value[:12]}...REDACTED"

    # QUERY_STRING is deliberately NOT an HTTP_ key in the WSGI environ, so the
    # HTTP_-prefix filter above already excludes it, and a key sent in the URL
    # never reaches WebhookLog.headers today. That is a consequence of the
    # prefix rather than a rule written anywhere; widening this filter to keep
    # non-HTTP_ environ keys would silently start storing raw keys. Any such
    # change must add QUERY_STRING to `skip` in the same edit.
    return headers


def coerce_form_wrapped_json(data):
    """
    Undo the shape a JSON body takes when it is declared as a form.

    A sender that posts `{"InvoiceNumber": "INV-1", ...}` but declares
    Content-Type application/x-www-form-urlencoded gets read by FormParser as a
    urlencoded pair list, and a body with no "=" in it becomes a single field
    NAME with an empty value, so `request.data` arrives as
    `{'{"InvoiceNumber": "INV-1", ...}': ''}`, the entire payload sitting in a
    dict key.

    When that exact shape is seen, meaning one item, an empty value, and a key
    that strips to something starting with "{" and ending with "}", the key is
    parsed as JSON and the resulting dict returned. Every other input is
    returned unchanged, including a genuine one-field form and a key that fails
    to parse.
    """
    if not isinstance(data, dict) or len(data) != 1:
        return data

    try:
        key, value = next(iter(data.items()))
    except (StopIteration, ValueError, TypeError):
        return data

    if value not in ("", [], [""], None):
        return data

    if not isinstance(key, str):
        return data

    candidate = key.strip()
    if not (candidate.startswith("{") and candidate.endswith("}")):
        return data

    try:
        parsed = json.loads(candidate)
    except ValueError:
        return data

    return parsed if isinstance(parsed, dict) else data


def validate_api_key(request, *, record_usage=True):
    """
    Validates the X-CRM-API-KEY value against the WebhookApiKey database table.
    The key is taken from extract_api_key(), so it may have arrived in the
    header or in the query string; the header wins when both are present.
    Returns (api_key_obj, None) on success or (None, error_str) on failure.
    Does NOT fall back to static key — caller handles that separately.

    record_usage=False validates without touching last_used_at or usage_count.
    A liveness GET is not a delivery, and counting it would make the usage
    figures on the keys page a mixture of two different things.
    """
    from .models import WebhookApiKey

    key_value, _transport = extract_api_key(request)
    if not key_value:
        return None, "missing"

    try:
        api_key = WebhookApiKey.objects.get(api_key=key_value)
    except WebhookApiKey.DoesNotExist:
        return None, "Invalid API key."

    if not api_key.is_active:
        return None, "This API key has been deactivated."

    # Soft narrowing only — NOT a security boundary, and never the thing that
    # grants access. A server-to-server sender transmits no Origin/Referer at all,
    # so this check is skipped entirely for exactly the traffic we expect; and a
    # caller who wanted past it would simply omit the header. The key is the
    # credential. Leave allowed_domains empty unless a specific sender is known to
    # be browser-originated.
    if api_key.allowed_domains:
        origin = (
            request.META.get("HTTP_ORIGIN", "") or
            request.META.get("HTTP_REFERER", "")
        )
        if origin:
            domain = urlparse(origin).netloc.split(":")[0]
            if domain and domain not in api_key.allowed_domains:
                return None, f"Domain '{domain}' is not authorised for this API key."

    if record_usage:
        api_key.record_usage()
    return api_key, None


def validate_webhook_secret(request):
    """
    Legacy static-secret validation via X-WEBHOOK-SECRET header.
    Returns (True, "") or (False, error_message).
    """
    incoming = request.META.get("HTTP_X_WEBHOOK_SECRET", "").strip()
    if not incoming:
        return False, "missing"

    expected = getattr(settings, "WEBHOOK_SECRET_KEY", "").strip()
    if not expected:
        return False, "Webhook authentication is not configured on this server."

    if incoming != expected:
        return False, "Invalid webhook secret."

    return True, ""


def authenticate_request(request, *, record_usage=True):
    """
    Try X-CRM-API-KEY (DB) first, then X-WEBHOOK-SECRET (legacy static).
    Returns (api_key_obj_or_None, error_str_or_None).

    The key is accepted in the X-CRM-API-KEY header or, failing that, in the
    query string. The query string exists so that ONE URL is a complete,
    testable integration. A URL can be pasted into a browser, a monitoring
    check or a curl line and handed to an external team with no header setup at
    all, which is the difference between a same-day test and a scheduling
    exercise.

    The cost is real and worth stating plainly. A key in a URL is written to
    reverse-proxy and gunicorn access logs, kept in browser history, and sent on
    in the Referer of anything the page links to. A key in a header is written
    to none of those. Treat a URL-carried key as disclosed to everyone who can
    read a log, and regenerate it when the test is finished.

    The header keeps absolute priority, so no integration that sends the header
    today can change behaviour because of the fallback.

    record_usage=False validates without bumping usage_count/last_used_at; see
    validate_api_key.

    There is deliberately NO Origin/Referer fallback. `Origin` and `Referer` are
    ordinary request headers: only a browser is bound to set them truthfully, and
    a webhook sender is not a browser. Authenticating on them meant anyone who
    could name one of our sending domains — public information — could post
    bookings with no key at all:

        curl -X POST .../ingest/ -H "Origin: https://one-of-our-sites.com"

    It also scaled the wrong way. Every domain added to CORS_ALLOWED_ORIGINS
    became another key-less way in, so the list we would have to grow to onboard
    senders was the same list that granted them access. CORS is a browser policy
    and cannot carry server-to-server authentication; the key does that.
    """
    api_key_obj, api_key_err = validate_api_key(request, record_usage=record_usage)
    if api_key_obj is not None:
        return api_key_obj, None

    # api_key_err == "missing" means no key was present on either carrier; try
    # the legacy secret
    if api_key_err == "missing":
        ok, secret_err = validate_webhook_secret(request)
        if ok:
            return None, None
        # A wrong secret now reports itself instead of being flattened into the
        # generic "authentication required" — those are different operator problems.
        if secret_err != "missing":
            return None, secret_err
        return None, (
            "Authentication required: send your key in the X-CRM-API-KEY header, "
            "or as an X-CRM-API-KEY query parameter on the URL."
        )

    return None, api_key_err


def unwrap_payload(data: dict) -> dict:
    """
    Handles Zoho Flow style wrapping where the actual data is inside
    webhookTrigger -> payload.
    Returns the inner payload if it exists, otherwise the original data.
    """
    if not isinstance(data, dict):
        return {}
    if "webhookTrigger" in data and isinstance(data["webhookTrigger"], dict):
        return data["webhookTrigger"].get("payload", data)
    return data
