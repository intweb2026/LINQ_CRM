"""
mcp_auth/authentication.py
──────────────────────────
Lets an MCP access token authenticate against the CRM API.

THE PROBLEM THIS SOLVES
The MCP tools reach the CRM over its own HTTP API, which is what keeps them
generic; one code path serves every endpoint and RBAC is enforced by DRF
exactly as it is for the web app. But the credential a tool now holds is an
OAuth access token minted by mcp_auth, and DRF's TokenAuthentication looks the
presented value up in authtoken_token, where it does not exist. Without this
class every tool call answers 401.

WHY A BEARER SCHEME, NOT "Token"
Keeping the two schemes distinct means a DRF token and an MCP token can never
be confused for one another, and a lookup failure in one is not silently
retried as the other. It also matches what the OAuth flow issues, so the token
is presented the way its own metadata says it should be.

WHY THIS ONE IS SAFE GLOBALLY, unlike dataapi's
dataapi/authentication.py carries a hard warning against ever appearing in
DEFAULT_AUTHENTICATION_CLASSES, because its key is a machine credential that
belongs to nobody and would bypass every per-user check. This is the opposite.
It resolves to a real User, so every permission class, RBAC scope and audit
line behaves as though that person made the request, which is the entire point
of the OAuth flow. It is registered LAST, so it cannot change the
WWW-Authenticate header on existing 401s; DRF takes that from the first
authenticator in the list.
"""
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .models import IssuedToken, hash_secret


class McpTokenAuthentication(BaseAuthentication):
    """Authenticate `Authorization: Bearer <mcp access token>`."""

    keyword = b"bearer"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != self.keyword:
            # Not ours. Return None so DRF falls through to the other
            # authenticators rather than failing the request outright.
            return None
        if len(auth) != 2:
            raise AuthenticationFailed("Invalid bearer header.")

        try:
            token = auth[1].decode()
        except UnicodeError:
            raise AuthenticationFailed("Invalid bearer token.")

        row = (IssuedToken.objects
               .select_related("user")
               .filter(token_hash=hash_secret(token), kind=IssuedToken.ACCESS)
               .first())
        if row is None:
            raise AuthenticationFailed("Invalid or expired token.")
        if row.is_expired():
            # Said plainly, because the client's correct response is to use its
            # refresh token rather than to prompt the person again.
            raise AuthenticationFailed("Token has expired.")

        user = row.user
        # Re-read on every request, exactly as mcp_auth.provider does. A token
        # must not outlive the account it belongs to, so deactivating someone
        # cuts off their live session instead of waiting for expiry.
        if not user.is_active or user.status != user.Status.ACTIVE:
            raise AuthenticationFailed("This account is no longer active.")

        return (user, row)

    def authenticate_header(self, request):
        return "Bearer"
