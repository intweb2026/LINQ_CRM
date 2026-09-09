"""
accounts/google_identity.py
───────────────────────────
The one place that turns a Google credential into a CRM user.

WHY IT EXISTS
GoogleTokenLoginView has been the only way into this CRM, and the MCP consent
page is now a second door onto the same building. Two doors enforcing their own
copies of the same four rules is how they end up disagreeing, and the way that
disagreement shows up is somebody getting in who should not. So the rules live
here once and both callers ask this function.

Nothing in the suite covered that logic before this module; see
accounts/tests_google_identity.py, which now does.

THE RULES, in order, and none of them is optional.
  1. The credential must verify against Google, signature, audience, expiry
  2. The email must be present and marked verified by Google
  3. Its domain must be allowed, when a domain allow list is configured
  4. It must match an existing User that is active AND has login_access

Rule 4 is the important one. No user is ever created here. A valid Google
account that nobody has provisioned is a refusal, not a new row.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

User = get_user_model()


class GoogleIdentityError(Exception):
    """
    A refusal, carrying the status the HTTP callers should answer with.

    The message is deliberately the same wording the login view already
    returned, so behaviour visible to a user does not change with this
    refactor.
    """

    def __init__(self, message, status=401):
        super().__init__(message)
        self.message = message
        self.status = status


def user_from_google_credential(credential: str):
    """
    Verify a Google ID token and return the CRM user it belongs to.

    Raises GoogleIdentityError on every refusal. Returns a User on success and
    nothing else; issuing a token, starting a session or minting an OAuth code
    is the caller's business, not this function's.
    """
    credential = (credential or "").strip()
    if not credential:
        raise GoogleIdentityError("Google credential is required.", 400)

    client_id = settings.GOOGLE_OAUTH_CLIENT_ID
    if not client_id:
        raise GoogleIdentityError(
            "Google Sign-In is not configured on this server.", 500)

    # 1. Verify with Google. verify_oauth2_token checks the signature against
    # Google's keys, the audience against our client id, and the expiry.
    try:
        idinfo = google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), client_id,
        )
    except ValueError:
        raise GoogleIdentityError("Invalid Google credential.", 401)

    # 2. A Google account can carry an unverified email. Trusting it would let
    # someone claim a colleague's address.
    email = (idinfo.get("email") or "").strip().lower()
    if not email or not idinfo.get("email_verified"):
        raise GoogleIdentityError("Google account email is not verified.", 401)

    # 3. Domain restriction, when configured. An empty list means no restriction.
    allowed = settings.GOOGLE_OAUTH_ALLOWED_DOMAINS
    if allowed and email.rsplit("@", 1)[-1] not in allowed:
        raise GoogleIdentityError(
            "Sign-in is restricted to organisation accounts.", 403)

    # 4. Match an existing, active, login-enabled account. Never create one.
    try:
        return User.objects.get(email__iexact=email, is_active=True,
                                login_access=True)
    except User.DoesNotExist:
        raise GoogleIdentityError(
            "No account with login access found for this email. "
            "Contact an administrator.", 403)
