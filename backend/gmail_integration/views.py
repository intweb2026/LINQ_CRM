import logging

from django.conf import settings
from django.shortcuts import redirect
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import is_super_admin

from . import service
from .models import GmailAccount

logger = logging.getLogger(__name__)


class GmailStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            account = GmailAccount.objects.get(user=request.user)
        except GmailAccount.DoesNotExist:
            # `can_connect` keeps the login prompt from offering a button that
            # cannot work; see components/GmailConnectPrompt.jsx.
            return Response({"connected": False,
                             "can_connect": not service.config_error()})
        return Response({
            "connected": True,
            "can_connect": True,
            "google_email": account.google_email,
            "connected_at": account.connected_at,
        })


class GmailConnectView(APIView):
    """
    The consent URL to send this user to.

    `return_to` is where the browser should land afterwards, so a connection
    started from the login prompt comes back to the page the user was on rather
    than to Pre-Event Docs. It rides in the SIGNED state parameter, not in a
    query string on the redirect URI: Google requires the redirect URI to match
    the registered one exactly, and state is the only round-trip channel that
    survives the hop. service.safe_return_to rejects anything that is not a
    site-relative path.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        problem = service.config_error()
        if problem:
            # 503, not 400: nothing is wrong with the request, the server is not
            # set up.
            #
            # TWO AUDIENCES, TWO MESSAGES. The technical reason names
            # environment variables and carries a shell command, which is the
            # right answer for whoever can fix it and useless noise to an SCA
            # who wanted to email some badges. It went to everybody at first,
            # and a screenshot of a Fernet key instruction inside a "Connect
            # Gmail" dialog is what that looks like. The full reason is always
            # logged, so it is never lost even when it is not shown.
            logger.error("Gmail connect refused, misconfigured: %s", problem)
            body = {"detail": "Sending email is not set up on the server yet. "
                              "Please ask an administrator to finish it."}
            if is_super_admin(request.user):
                body["detail"] = problem
                body["admin_only"] = True
            return Response(body, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"authorize_url": service.build_auth_url(
            request.user.id, request.query_params.get("return_to", ""),
        )})


class GmailCallbackView(APIView):
    """
    Unauthenticated by necessity: Google redirects the browser here directly,
    carrying no auth header. The state param (see service.make_state) is a
    signed, short-lived claim of who initiated the connect, which is why it is
    trusted instead of request.user.
    """
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        from django.contrib.auth import get_user_model

        code = request.query_params.get("code")
        state = request.query_params.get("state")
        claim = service.read_state(state) if state else None

        # EVERY FAILURE BELOW IS LOGGED WITH ITS CAUSE.
        #
        # All of them used to answer the same bare error redirect, which the
        # browser renders as "Gmail could not be connected. Please try again."
        # That message is right for the user and useless for anybody debugging,
        # and three of these four paths wrote nothing anywhere, so a failed
        # connection left no trace at all. The user-facing text is unchanged;
        # what changes is that the reason now exists somewhere.
        if not code or not claim:
            logger.error("Gmail callback rejected: %s",
                         "no code in the callback" if not code
                         else "state missing, expired past its 10 minutes, or "
                              "failed its signature")
            return redirect(_return_url(error=True))
        return_to = claim["return_to"]

        User = get_user_model()
        try:
            user = User.objects.get(id=claim["user_id"])
        except User.DoesNotExist:
            logger.error("Gmail callback: user %s in the signed state no "
                         "longer exists", claim["user_id"])
            return redirect(_return_url(error=True, return_to=return_to))

        try:
            tokens = service.exchange_code(code)
        except Exception:
            # The likeliest single point of failure in the whole flow, and it
            # was the one with no logging. Causes seen here: a redirect URI that
            # differs from the one registered, a wrong or rotated client secret,
            # a code already redeemed, and oauthlib refusing a token whose scope
            # came back as a superset of the request.
            logger.exception("Gmail callback: the authorization code could not "
                             "be exchanged for tokens (user %s)", user.pk)
            return redirect(_return_url(error=True, return_to=return_to))

        if not tokens.get("refresh_token"):
            # No refresh token means the user has connected before and Google
            # skipped consent despite prompt=consent (rare, but possible if the
            # app has been pre-authorized at the workspace level). Without one
            # there is nothing to refresh with later, so surface it as an error
            # rather than silently storing an account that stops working in an
            # hour.
            logger.error("Gmail callback: Google returned no refresh token for "
                         "user %s, so nothing was stored. Revoke the CRM's "
                         "access at myaccount.google.com/permissions and "
                         "connect again.", user.pk)
            return redirect(_return_url(error=True, return_to=return_to))

        if not tokens.get("google_email"):
            logger.error("Gmail callback: the account's email address could not "
                         "be determined for user %s, so the From address would "
                         "be blank. Check that openid and userinfo.email are on "
                         "the OAuth consent screen.", user.pk)
            return redirect(_return_url(error=True, return_to=return_to))

        # The user has already consented by this point, so a failure here is
        # not theirs to interpret; it is logged with the cause and reported as
        # the same error redirect. Encryption is the realistic way to get here,
        # an unusable GMAIL_TOKEN_ENCRYPTION_KEY raising ValueError, which used
        # to surface as a 500 on the callback with a granted token discarded.
        try:
            account, _ = GmailAccount.objects.get_or_create(user=user)
            account.google_email = tokens["google_email"]
            account.set_refresh_token(tokens["refresh_token"])
            account.access_token = tokens["access_token"] or ""
            account.token_expiry = tokens["token_expiry"]
            account.scopes = tokens["scopes"]
            account.save()
        except Exception:
            logger.exception("Gmail consent succeeded but the token could not "
                             "be stored for user %s", user.pk)
            return redirect(_return_url(error=True, return_to=return_to))

        return redirect(_return_url(connected=True, return_to=return_to))


class GmailDisconnectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        GmailAccount.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _return_url(connected=False, error=False, return_to=""):
    """
    Where the browser lands after the hop through Google.

    The flag in the query string is what tells the app the round trip happened;
    components/GmailConnectPrompt.jsx reads it and reports the outcome. Without
    it a user who has just clicked through a Google consent screen is returned
    to a page that says nothing at all about whether it worked.

    Pre-Event Docs is the fallback rather than the rule, being where this could
    be started from before there was a login prompt anywhere else.
    """
    base = service.safe_return_to(return_to) or "/pre-event-docs/check-in"
    separator = "&" if "?" in base else "?"
    if connected:
        return f"{base}{separator}gmail_connected=1"
    if error:
        return f"{base}{separator}gmail_error=1"
    return base
