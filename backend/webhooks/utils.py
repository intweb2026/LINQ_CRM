"""
webhooks/utils.py
──────────────────
Shared helpers: IP extraction, header sanitisation, key validation.
"""
from urllib.parse import urlparse

from django.conf import settings


def extract_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    val = forwarded or request.META.get("REMOTE_ADDR", "")
    return val if val else None


def safe_headers(meta: dict) -> dict:
    """HTTP_* headers from request.META, stripping all secret/key values."""
    skip = {"HTTP_X_WEBHOOK_SECRET", "HTTP_X_API_KEY", "HTTP_X_CRM_API_KEY"}
    return {
        k: v for k, v in meta.items()
        if k.startswith("HTTP_") and k not in skip
    }


def validate_api_key(request):
    """
    Validates X-CRM-API-KEY against the WebhookApiKey database table.
    Returns (api_key_obj, None) on success or (None, error_str) on failure.
    Does NOT fall back to static key — caller handles that separately.
    """
    from .models import WebhookApiKey

    key_value = request.META.get("HTTP_X_CRM_API_KEY", "").strip()
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


def authenticate_request(request):
    """
    Try X-CRM-API-KEY (DB) first, then X-WEBHOOK-SECRET (legacy static).
    Returns (api_key_obj_or_None, error_str_or_None).

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
    api_key_obj, api_key_err = validate_api_key(request)
    if api_key_obj is not None:
        return api_key_obj, None

    # api_key_err == "missing" means the header wasn't present; try the legacy secret
    if api_key_err == "missing":
        ok, secret_err = validate_webhook_secret(request)
        if ok:
            return None, None
        # A wrong secret now reports itself instead of being flattened into the
        # generic "authentication required" — those are different operator problems.
        if secret_err != "missing":
            return None, secret_err
        return None, "Authentication required: send your key in the X-CRM-API-KEY header."

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
