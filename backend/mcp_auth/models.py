"""
mcp_auth/models.py
──────────────────
Storage for the OAuth authorization server that fronts the MCP endpoint.

WHY AN AUTHORIZATION SERVER AT ALL
claude.ai's connector dialog takes a URL and nothing else. It discovers how to
authenticate from the server, and it registers itself dynamically (RFC 7591).
Google does not offer dynamic client registration, so the CRM cannot simply
point at Google; it has to be the authorization server itself and authenticate
the person via Google underneath. That is what accounts.GoogleTokenLoginView
already does for the web app, and the consent view reuses those exact rules.

WHY NOT django-oauth-toolkit
The MCP SDK already implements every HTTP endpoint, metadata, /authorize,
/token, /register and /revoke, and asks only for storage behind a ten method
provider protocol. Four small tables answer that. Bridging a full OAuth library
into the same protocol would be more code, not less, plus a dependency.

USER CREDENTIALS ARE STORED HASHED, following dataapi.DataApiKey. Authorization
codes, access tokens and refresh tokens are kept as SHA-256 digests, the raw
value exists only in the response that issues it, and lookups hash the
presented value and match on the digest column. A leaked database row cannot be
replayed against the endpoint.

THE ONE EXCEPTION IS OAuthClient.client_secret, which the SDK compares
directly and so must be stored as issued. That field carries the reasoning.
"""
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def hash_secret(raw: str) -> str:
    """One hashing rule for every credential in this module."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_secret(nbytes: int = 40) -> str:
    return secrets.token_urlsafe(nbytes)


class OAuthClient(models.Model):
    """
    A client registered through /register, in practice claude.ai.

    Registration is open, which is what dynamic client registration means, and
    it is not the security boundary; registering only lets a client ASK for
    authorization. Nothing is granted until a real person signs in with Google
    and consents, and the token that results carries that person's CRM
    permissions. Redirect URIs are recorded at registration and enforced at
    /authorize, so a client cannot later divert a code somewhere else.
    """
    client_id = models.CharField(max_length=64, unique=True, db_index=True)
    # STORED AS ISSUED, NOT HASHED, and that is deliberate.
    #
    # The SDK compares the presented secret against this value directly, so a
    # digest cannot be used; hashing it meant get_client() had to return None,
    # the SDK judged the client misconfigured, and every token exchange failed
    # the instant the browser came back from Google. That is what happened in
    # production.
    #
    # It cannot be avoided by registering public clients either: the SDK's
    # /register defaults a missing token_endpoint_auth_method to
    # client_secret_post and mints a secret, and its metadata never advertises
    # "none", so a client following the metadata always ends up confidential.
    #
    # The exposure is small and worth being explicit about. This secret
    # authenticates a CLIENT APPLICATION, not a person. On its own it grants
    # nothing: every grant still needs someone to sign in with Google and
    # consent, and the resulting token carries only that person's CRM
    # permissions. DRF's own authtoken table in this same database stores its
    # keys in the clear, and those are far more powerful.
    client_secret = models.CharField(max_length=128, blank=True, default="")
    client_name = models.CharField(max_length=255, blank=True, default="")
    # How the client proves itself at /token. STORED, not defaulted at read
    # time: omitting it meant get_client() handed the SDK a Python None, which
    # its client-auth middleware does not recognise, and every token exchange
    # died with "Unsupported auth method: None".
    token_endpoint_auth_method = models.CharField(max_length=32, default="none")
    redirect_uris = models.JSONField(default=list)
    grant_types = models.JSONField(default=list)
    response_types = models.JSONField(default=list)
    scope = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "MCP OAuth client"

    def __str__(self):
        return f"{self.client_name or 'client'} ({self.client_id})"


class AuthorizationCode(models.Model):
    """
    A one-shot code, exchanged at /token for an access token.

    SINGLE USE IS ENFORCED BY DELETION, not by a spent flag. A replayed code
    then finds nothing rather than finding a row whose flag someone forgot to
    check, which is the failure mode that makes stolen codes useful.
    """
    code_hash = models.CharField(max_length=64, unique=True, db_index=True)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE,
                               related_name="codes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="mcp_auth_codes")
    redirect_uri = models.URLField(max_length=500)
    # Whether the client sent redirect_uri explicitly. RFC 6749 requires the
    # token request to repeat it only when the authorize request included it,
    # and the SDK asks us for this flag rather than inferring it.
    redirect_uri_provided_explicitly = models.BooleanField(default=True)
    code_challenge = models.CharField(max_length=255)
    scopes = models.JSONField(default=list)
    resource = models.CharField(max_length=500, blank=True, default="")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    # Short on purpose. A code is exchanged within seconds by a machine; the
    # only thing a longer window buys is a wider replay opportunity.
    LIFETIME_SECONDS = 300

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at


class IssuedToken(models.Model):
    """
    An access or refresh token. One table, because they differ only in lifetime
    and in which endpoint accepts them, and two near-identical tables would
    drift.

    Revocation cascades. Deleting the refresh token of a session must not leave
    its access tokens usable, so access rows point at the refresh row that
    minted them and go with it.
    """
    ACCESS = "access"
    REFRESH = "refresh"
    KIND_CHOICES = [(ACCESS, "Access"), (REFRESH, "Refresh")]

    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, db_index=True)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE,
                               related_name="tokens")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="mcp_auth_tokens")
    scopes = models.JSONField(default=list)
    resource = models.CharField(max_length=500, blank=True, default="")
    parent = models.ForeignKey("self", null=True, blank=True,
                               on_delete=models.CASCADE, related_name="children")
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    ACCESS_LIFETIME_SECONDS = 60 * 60
    REFRESH_LIFETIME_SECONDS = 60 * 60 * 24 * 30

    class Meta:
        indexes = [models.Index(fields=["user", "kind"])]

    def is_expired(self) -> bool:
        return self.expires_at is not None and timezone.now() >= self.expires_at


class PendingAuthorization(models.Model):
    """
    An /authorize request parked while the person signs in with Google.

    The authorize step cannot answer immediately, because the CRM has to find
    out who is asking, and that means a browser round trip. The request is held
    here under an unguessable id, the consent page is handed that id, and the
    code is only minted once Google has verified the person and the CRM has
    matched them to an account with login access.

    Nothing here is trusted back from the browser. The redirect URI, scopes and
    code challenge are read from this row at completion, not from the callback,
    so a tampered consent URL cannot change where the code goes.
    """
    tx_hash = models.CharField(max_length=64, unique=True, db_index=True)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE,
                               related_name="pending")
    redirect_uri = models.URLField(max_length=500)
    redirect_uri_provided_explicitly = models.BooleanField(default=True)
    code_challenge = models.CharField(max_length=255)
    state = models.CharField(max_length=500, blank=True, default="")
    scopes = models.JSONField(default=list)
    resource = models.CharField(max_length=500, blank=True, default="")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    # Long enough for a person to complete a Google sign-in, short enough that
    # an abandoned tab does not leave a usable handle lying about.
    LIFETIME_SECONDS = 600

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at
