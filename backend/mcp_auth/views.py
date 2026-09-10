"""
mcp_auth/views.py
─────────────────
The consent page, the only part of the OAuth flow a person ever sees.

WHERE THIS SITS
The SDK's /authorize hands off to mcp_auth.provider.authorize(), which parks the
request as a PendingAuthorization and redirects the browser here. This page
signs the person in with Google, mints the authorization code, and sends the
browser back to the client's redirect URI. The SDK then handles /token.

NOTHING FROM THE BROWSER IS TRUSTED. The redirect URI, the scopes, the state and
the PKCE challenge are all read back from the parked row, never from the request
that arrives here. A tampered consent URL can therefore change nothing except
which parked row is being completed, and that lookup is by an unguessable
token that expires.

These paths live under /api/ deliberately. The React build is served by a
separate Node process in production, and it forwards a fixed list of prefixes to
Django, /api among them, see frontend/scripts/serve-build.mjs. Anything outside
that list is answered with the SPA shell instead of reaching this file.
"""
from datetime import timedelta
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.google_identity import GoogleIdentityError, user_from_google_credential

from .models import AuthorizationCode, PendingAuthorization, hash_secret, new_secret


def _pending(tx):
    """The parked authorize request for this token, or None if it cannot be used."""
    if not tx:
        return None
    row = (PendingAuthorization.objects
           .select_related("client")
           .filter(tx_hash=hash_secret(tx))
           .first())
    if row is None or row.is_expired():
        return None
    return row


def _redirect_with(redirect_uri, **params):
    """
    Append parameters to the client's redirect URI, keeping any it already has.

    Built with urlparse rather than string concatenation because a redirect URI
    is allowed to carry its own query string, and gluing "?code=" onto one that
    already contains a "?" produces a URL the client cannot parse.
    """
    parts = urlparse(redirect_uri)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: v for k, v in params.items() if v})
    return urlunparse(parts._replace(query=urlencode(query)))


@require_GET
def consent(request):
    """
    GET /api/mcp-auth/consent/?tx=… — sign in and approve.

    Renders whether or not the token is good. An invalid or expired handle is
    shown as a plain message rather than an exception, because the person
    seeing it has done nothing wrong; the usual cause is a tab left open past
    the ten minute window.
    """
    pending = _pending(request.GET.get("tx"))
    response = render(request, "mcp_auth/consent.html", {
        "tx": request.GET.get("tx", ""),
        "pending": pending,
        "client_name": pending.client.client_name if pending else "",
        "google_client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
    })
    # WITHOUT THIS, GOOGLE SIGN-IN SILENTLY HANGS.
    #
    # SecurityMiddleware stamps every Django response with
    # Cross-Origin-Opener-Policy: same-origin, which severs window.opener for
    # cross-origin popups. Google Identity Services opens accounts.google.com
    # in a popup and hands the credential back by posting a message to the
    # window that opened it; under same-origin that link does not exist, so the
    # popup completes, goes blank on /gsi/transform, and nothing further
    # happens. No error is raised anywhere, which is what makes it hard to see.
    #
    # The CRM's own login never hit this because the React build is served by
    # the Node frontend, which sets no COOP header at all. This page is served
    # by Django, so it inherits one.
    #
    # same-origin-allow-popups is the documented value for a page that opens an
    # OAuth popup. It keeps the protection that matters, other origins still
    # cannot get a handle on this window, and only restores this page's
    # reference to popups it opened itself. Set here rather than in settings so
    # the relaxation applies to this one page and not the whole CRM;
    # SecurityMiddleware leaves the header alone when a view has already set it.
    response["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    return response


@require_POST
def complete(request):
    """
    POST /api/mcp-auth/consent/complete/ — verify the person, mint the code.

    Answers JSON rather than a 302 because the caller is the fetch() on the
    consent page, and a redirect there would be followed by the browser behind
    the script's back. The page navigates to the returned URL itself.
    """
    tx = request.POST.get("tx", "")
    pending = _pending(tx)
    if pending is None:
        return JsonResponse(
            {"detail": "This sign-in request has expired. Start again from Claude."},
            status=400,
        )

    try:
        user = user_from_google_credential(request.POST.get("credential", ""))
    except GoogleIdentityError as exc:
        # Same rules, same wording, as the web app's login. See
        # accounts/google_identity.py.
        return JsonResponse({"detail": exc.message}, status=exc.status)

    code = new_secret(32)
    AuthorizationCode.objects.create(
        code_hash=hash_secret(code),
        client=pending.client,
        user=user,
        redirect_uri=pending.redirect_uri,
        redirect_uri_provided_explicitly=pending.redirect_uri_provided_explicitly,
        code_challenge=pending.code_challenge,
        scopes=pending.scopes,
        resource=pending.resource,
        expires_at=timezone.now() + timedelta(
            seconds=AuthorizationCode.LIFETIME_SECONDS),
    )
    # The parked row has done its job. Deleting it here is what stops one
    # authorize request being completed twice, by a replayed consent URL or by
    # a double submit.
    redirect_uri, state = pending.redirect_uri, pending.state
    pending.delete()

    return JsonResponse({"redirect": _redirect_with(redirect_uri, code=code, state=state)})
