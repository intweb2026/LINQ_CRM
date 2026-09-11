"""
gmail_integration/service.py
──────────────────────────────
OAuth-with-offline-access for one scope, gmail.send, and nothing else. This is
NOT accounts/google_identity.py's ID-token login check; that verifies who
somebody is, once, and carries no ability to send mail on their behalf. This
module gets a REFRESH TOKEN so the CRM can send from the user's own Gmail
account later, unattended, which is a different Google product (OAuth consent,
not Sign In With Google) and needs a client secret besides the existing client
id.

NO MAILBOX ACCESS. gmail.send cannot read a mailbox, list messages or modify
labels; the feature never needs to, so nothing broader is requested. The two
identity scopes beside it, openid and userinfo.email, exist only to learn WHICH
address the refresh token belongs to. That address is the From on every message
and the one shown on the settings row, and there is no way to obtain it from
gmail.send alone: Gmail's users.getProfile requires a READ scope, which is a
far bigger grant than knowing an email address. The CRM already learns the same
address at login.
"""
import base64
import logging
import mimetypes
import os
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from django.conf import settings
from django.core import signing
from django.utils import timezone
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from .models import GmailAccount

# WHY THE TOKEN-SCOPE CHECK IS RELAXED, set before oauthlib is used.
#
# oauthlib refuses a token response whose scope is not exactly what was asked
# for, raising Warning("Scope has changed from ... to ..."). Google routinely
# answers with a SUPERSET: this client id is also the CRM's Sign-In client, so
# an account that has signed in has already granted it profile scopes, and they
# come back in the token whether or not this flow asked. That refusal surfaced
# as "Gmail could not be connected. Please try again." with the real cause
# swallowed by a bare except.
#
# Relaxing it is not a loss of enforcement. The scope that governs what the
# credential can DO is the one Google recorded at consent, not the string
# oauthlib compares, and a superset here is Google reporting grants the user
# had already made. What the CRM will actually use is fixed in code, one call
# to messages.send.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    # Identity only, so the stored token can be attributed to an address. See
    # the module docstring for why gmail.send cannot supply it.
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

STATE_SALT = "gmail_integration.oauth_state"

logger = logging.getLogger(__name__)


class GmailNotConnected(Exception):
    pass


class GmailAuthExpired(Exception):
    pass


def _client_config():
    return {
        "web": {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [settings.GMAIL_OAUTH_REDIRECT_URI],
        }
    }


def config_error():
    r"""
    Why this deployment cannot connect a Gmail account, or "" if it can.

    CHECKED BEFORE THE USER IS SENT TO GOOGLE, because every one of these
    failures is otherwise discovered at the worst possible moment. A wrong
    redirect URI shows the user Google's own "Access blocked, this app's
    request is invalid" screen, which names no cause they can act on. A bad
    Fernet key is worse: consent SUCCEEDS, Google hands back a real refresh
    token, and the save then dies on an unhandled ValueError, so the user has
    granted access and has nothing to show for it.

    The Fernet key is constructed rather than length-checked. Fernet wants 32
    url-safe base64 bytes, 44 characters ending in "=", and the ways to get
    that wrong are not all visible in a length; a truncated paste, a stray
    quote, and a client secret pasted into the wrong variable all fail here.
    """
    if not settings.GOOGLE_OAUTH_CLIENT_ID:
        return "GOOGLE_OAUTH_CLIENT_ID is not set."
    if not settings.GOOGLE_OAUTH_CLIENT_SECRET:
        return ("GOOGLE_OAUTH_CLIENT_SECRET is not set. The Gmail send flow "
                "needs one, unlike Google Sign-In.")
    if not settings.GMAIL_OAUTH_REDIRECT_URI:
        return "GMAIL_OAUTH_REDIRECT_URI is not set."

    key = getattr(settings, "GMAIL_TOKEN_ENCRYPTION_KEY", "") or ""
    if not key:
        return ("GMAIL_TOKEN_ENCRYPTION_KEY is not set, so a refresh token "
                "could not be stored.")
    try:
        from cryptography.fernet import Fernet

        Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return ("GMAIL_TOKEN_ENCRYPTION_KEY is not a valid Fernet key. It must "
                "be 32 url-safe base64-encoded bytes, which is 44 characters "
                "ending in '='. Generate one with: python -c \"from "
                "cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"")
    return ""


def _flow():
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = settings.GMAIL_OAUTH_REDIRECT_URI
    return flow


def safe_return_to(path):
    r"""
    A caller-supplied return path, reduced to something safe to redirect to, or
    "" if it is not.

    THIS IS AN OPEN-REDIRECT GUARD, and it is needed because the value arrives
    from the browser. Only a site-relative path is allowed: one leading slash,
    and no second one, since "//evil.example" is a protocol-relative URL that
    sends the browser off-site while looking like a path. A backslash is
    rejected for the same reason, some browsers normalising "/\evil.example"
    the same way. Anything with a scheme fails the leading-slash test already.
    """
    path = (path or "").strip()
    if not path.startswith("/"):
        return ""
    if path.startswith("//") or path.startswith("/\\"):
        return ""
    return path


def make_state(user_id, return_to=""):
    return signing.dumps(
        {"user_id": user_id, "return_to": safe_return_to(return_to)},
        salt=STATE_SALT,
    )


def read_state(state):
    """
    {"user_id", "return_to"} out of `state`, or None if it does not verify.

    return_to is re-checked on the way out, not trusted because it was signed:
    the signature proves this server minted the value, which is not the same as
    the value being safe, and re-running the guard costs nothing.
    """
    try:
        data = signing.loads(state, salt=STATE_SALT, max_age=600)
    except signing.BadSignature:
        return None
    if not data.get("user_id"):
        return None
    return {
        "user_id": data["user_id"],
        "return_to": safe_return_to(data.get("return_to")),
    }


def build_auth_url(user_id, return_to=""):
    flow = _flow()
    url, _ = flow.authorization_url(
        access_type="offline",
        # Forces a fresh refresh token even for somebody who has connected
        # before. Without it Google skips consent on a repeat authorization and
        # returns no refresh token at all, which this flow cannot use.
        prompt="consent",
        # NOT include_granted_scopes. Incremental authorization asks Google to
        # fold every scope this client already holds into the new token, which
        # is precisely the superset that made oauthlib refuse the exchange, and
        # this flow has no use for scopes it did not ask for.
        state=make_state(user_id, return_to),
    )
    return url


def _email_for(creds):
    """
    The address these credentials belong to, or "" if it cannot be determined.

    THREE SOURCES, IN ORDER OF COST. The id_token is already in hand when
    "openid" was granted, so it is free; note that google-auth may hand it back
    as a JWT STRING rather than a decoded dict, which the original code did not
    allow for, and a string silently failed the isinstance check and fell
    through. The userinfo endpoint is one extra request and needs
    userinfo.email. Neither is guaranteed, so the caller decides what a blank
    means rather than this raising.
    """
    token = getattr(creds, "id_token", None)
    if isinstance(token, dict) and token.get("email"):
        return token["email"]
    if isinstance(token, str) and token:
        try:
            from google.oauth2 import id_token as google_id_token

            claims = google_id_token.verify_oauth2_token(
                token, GoogleRequest(), settings.GOOGLE_OAUTH_CLIENT_ID,
            )
            if claims.get("email"):
                return claims["email"]
        except Exception:
            logger.warning("Gmail connect: id_token present but unreadable; "
                           "falling back to the userinfo endpoint",
                           exc_info=True)

    try:
        resp = requests.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {creds.token}"}, timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("email", "") or ""
    except Exception:
        # Not fatal on its own. exchange_code reports the blank and the caller
        # decides; previously this raise_for_status turned a missing address
        # into an unexplained "could not be connected".
        logger.warning("Gmail connect: userinfo lookup failed", exc_info=True)
        return ""


def exchange_code(code):
    flow = _flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    email = _email_for(creds)

    return {
        "google_email": email,
        "refresh_token": creds.refresh_token,
        "access_token": creds.token,
        "token_expiry": creds.expiry,
        "scopes": " ".join(creds.scopes or SCOPES),
    }


def is_connected(user):
    return GmailAccount.objects.filter(user=user).exists()


def _credentials(account):
    return Credentials(
        token=account.access_token or None,
        refresh_token=account.get_refresh_token(),
        token_uri=TOKEN_URI,
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=SCOPES,
    )


def get_valid_access_token(user):
    try:
        account = GmailAccount.objects.get(user=user)
    except GmailAccount.DoesNotExist:
        raise GmailNotConnected("No Gmail account connected for this user.")

    if account.access_token and account.token_expiry and account.token_expiry > timezone.now():
        return account.access_token

    creds = _credentials(account)
    try:
        creds.refresh(GoogleRequest())
    except Exception as exc:
        raise GmailAuthExpired(
            "Gmail access could not be refreshed. Please reconnect Gmail."
        ) from exc

    account.access_token = creds.token
    account.token_expiry = creds.expiry
    account.save(update_fields=["access_token", "token_expiry", "updated_at"])
    return creds.token


def _mime_message(user, to_email, subject, html_body, text_body, attachments,
                  inline_images):
    account = GmailAccount.objects.get(user=user)
    msg = MIMEMultipart("mixed")
    msg["To"] = to_email
    msg["From"] = account.google_email
    msg["Subject"] = subject

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body, "plain"))
    alt.attach(MIMEText(html_body, "html"))

    if inline_images:
        # multipart/related is what binds the HTML to the images its
        # <img src="cid:…"> tags name. Attached to mixed directly they arrive as
        # ordinary attachments and the body shows a broken image instead. A
        # data: URI is the obvious alternative and Gmail strips it.
        related = MIMEMultipart("related")
        related.attach(alt)
        for image in inline_images:
            part = MIMEImage(image["content"])
            part.add_header("Content-ID", f"<{image['cid']}>")
            part.add_header("Content-Disposition", "inline",
                            filename=image.get("filename", f"{image['cid']}.png"))
            related.attach(part)
        msg.attach(related)
    else:
        msg.attach(alt)

    for attachment in attachments or []:
        filename = attachment["filename"]
        content = attachment["content"]
        mimetype = attachment.get("mimetype") or (
            mimetypes.guess_type(filename)[0] or "application/octet-stream"
        )
        maintype, subtype = mimetype.split("/", 1)
        part = MIMEApplication(content, _subtype=subtype)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    return msg


def send_email(user, to_email, subject, html_body, text_body, attachments=None,
               inline_images=None):
    """
    Send one email through the caller's own Gmail account.

    Raises GmailNotConnected / GmailAuthExpired the same as
    get_valid_access_token, and lets googleapiclient.errors.HttpError propagate
    for the caller to classify per-recipient failures.
    """
    token = get_valid_access_token(user)
    creds = Credentials(token=token)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    msg = _mime_message(user, to_email, subject, html_body, text_body,
                        attachments, inline_images)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()
