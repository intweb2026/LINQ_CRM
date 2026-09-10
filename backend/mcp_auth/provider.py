"""
mcp_auth/provider.py
────────────────────
The OAuthAuthorizationServerProvider the MCP SDK calls.

The SDK owns every HTTP endpoint, metadata, /authorize, /token, /register and
/revoke. This file owns storage and identity, and nothing else. Each method
below is async because the SDK awaits it, and each wraps its ORM work in
sync_to_async because Django's ORM is synchronous.

THE ONE METHOD THAT IS NOT BOOKKEEPING is authorize(). It cannot answer on the
spot, because the CRM does not yet know who is asking. It parks the request and
returns the URL of a consent page; the code is minted later, by
mcp_auth.views.complete, once Google has verified the person and the CRM has
matched them to an account with login access. See models.PendingAuthorization.
"""
import logging
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode as SdkAuthorizationCode,
    AuthorizationParams,
    RefreshToken as SdkRefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .models import (
    AuthorizationCode,
    IssuedToken,
    OAuthClient,
    PendingAuthorization,
    hash_secret,
    new_secret,
)

logger = logging.getLogger(__name__)


def _resource(requested):
    """
    The audience to stamp on a grant, defaulting to this server.

    THIS SERVER SERVES EXACTLY ONE RESOURCE, and AuthSettings sets
    validate_token_resource, so the bearer middleware refuses any token whose
    resource is not resource_server_url. Its comparison parses the value as a
    URL, and an absent one parses as the empty string, which raises and counts
    as a mismatch. See mcp/server/auth/middleware/bearer_auth.py.

    RFC 8707's resource indicator is OPTIONAL and claude.ai does not send it.
    So without this default every token the server issued was refused the
    first time it was used, which surfaced as "Authorization with iQ hub App
    failed" and nothing in the flow before that looking wrong at all.

    Defaulting rather than disabling the check keeps it meaningful: a token
    minted elsewhere, for some other audience, is still rejected.
    """
    return requested or f"{settings.MCP_PUBLIC_URL}/mcp"


def _client_to_sdk(row: OAuthClient) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=row.client_id,
        # Returned as issued. The SDK compares the presented secret against
        # this value directly, so anything derived from it refuses every
        # confidential client, which is what the SDK's own /register creates by
        # default. See the model field for why storing it is acceptable.
        client_secret=row.client_secret or None,
        client_name=row.client_name,
        # Carried through rather than defaulted. See the model field.
        token_endpoint_auth_method=row.token_endpoint_auth_method or "none",
        redirect_uris=row.redirect_uris,
        grant_types=row.grant_types or ["authorization_code", "refresh_token"],
        response_types=row.response_types or ["code"],
        scope=row.scope or None,
    )


class DjangoOAuthProvider:
    """Backed by the four tables in mcp_auth.models."""

    # ── Clients ─────────────────────────────────────────────────────────────

    async def get_client(self, client_id: str):
        @sync_to_async
        def lookup():
            row = OAuthClient.objects.filter(client_id=client_id).first()
            return _client_to_sdk(row) if row else None

        return await lookup()

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # The SDK's /register defaults a missing token_endpoint_auth_method to
        # client_secret_post and mints a secret, and its metadata never
        # advertises "none", so a client following the metadata always ends up
        # confidential whether it wanted to or not. Both shapes work.
        method = client_info.token_endpoint_auth_method or "none"
        logger.info(
            "MCP client registered, %r, auth method %r, secret issued %s",
            client_info.client_name or client_info.client_id, method,
            bool(client_info.client_secret),
        )

        @sync_to_async
        def store():
            OAuthClient.objects.update_or_create(
                client_id=client_info.client_id,
                defaults={
                    "client_secret": client_info.client_secret or "",
                    "client_name": client_info.client_name or "",
                    "token_endpoint_auth_method": method,
                    "redirect_uris": [str(u) for u in client_info.redirect_uris],
                    "grant_types": list(client_info.grant_types or []),
                    "response_types": list(client_info.response_types or []),
                    "scope": client_info.scope or "",
                },
            )

        await store()

    # ── Authorization ───────────────────────────────────────────────────────

    async def authorize(self, client: OAuthClientInformationFull,
                        params: AuthorizationParams) -> str:
        """
        Park the request and send the browser to the consent page.

        Everything the token exchange will need is written here, not carried in
        the URL, so nothing coming back from the browser can change where the
        code is delivered or what it is worth.
        """
        from django.conf import settings as dj

        tx = new_secret(32)

        @sync_to_async
        def park():
            row = OAuthClient.objects.get(client_id=client.client_id)
            PendingAuthorization.objects.create(
                tx_hash=hash_secret(tx),
                client=row,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                code_challenge=params.code_challenge,
                state=params.state or "",
                scopes=list(params.scopes or []),
                resource=_resource(params.resource),
                expires_at=timezone.now() + timedelta(
                    seconds=PendingAuthorization.LIFETIME_SECONDS),
            )

        await park()
        base = getattr(dj, "MCP_PUBLIC_URL", "").rstrip("/")
        return f"{base}/api/mcp-auth/consent/?tx={tx}"

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str):
        @sync_to_async
        def lookup():
            row = (AuthorizationCode.objects
                   .select_related("client")
                   .filter(code_hash=hash_secret(authorization_code),
                           client__client_id=client.client_id)
                   .first())
            if row is None or row.is_expired():
                return None
            return SdkAuthorizationCode(
                code=authorization_code,
                scopes=row.scopes,
                expires_at=row.expires_at.timestamp(),
                client_id=client.client_id,
                code_challenge=row.code_challenge,
                redirect_uri=row.redirect_uri,
                redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
                resource=_resource(row.resource),
                subject=row.user.username,
            )

        return await lookup()

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code) -> OAuthToken:
        access_raw = new_secret()
        refresh_raw = new_secret()

        @sync_to_async
        def swap():
            row = (AuthorizationCode.objects
                   .select_related("client", "user")
                   .get(code_hash=hash_secret(authorization_code.code)))
            user, client_row = row.user, row.client
            scopes, resource = row.scopes, row.resource
            # Deleted, not flagged. A replay then finds nothing at all.
            row.delete()

            now = timezone.now()
            refresh = IssuedToken.objects.create(
                token_hash=hash_secret(refresh_raw), kind=IssuedToken.REFRESH,
                client=client_row, user=user, scopes=scopes, resource=resource,
                expires_at=now + timedelta(
                    seconds=IssuedToken.REFRESH_LIFETIME_SECONDS),
            )
            IssuedToken.objects.create(
                token_hash=hash_secret(access_raw), kind=IssuedToken.ACCESS,
                client=client_row, user=user, scopes=scopes, resource=resource,
                parent=refresh,
                expires_at=now + timedelta(
                    seconds=IssuedToken.ACCESS_LIFETIME_SECONDS),
            )
            return scopes

        scopes = await swap()
        return OAuthToken(
            access_token=access_raw,
            token_type="Bearer",
            expires_in=IssuedToken.ACCESS_LIFETIME_SECONDS,
            scope=" ".join(scopes),
            refresh_token=refresh_raw,
        )

    # ── Tokens ──────────────────────────────────────────────────────────────

    async def load_access_token(self, token: str):
        """
        Replaces DrfTokenVerifier. Same account checks, different credential.

        A token outlives nothing. The account state is re-read on every call, so
        deactivating someone in the CRM cuts off their live MCP session rather
        than waiting for the token to expire.
        """
        @sync_to_async
        def lookup():
            row = (IssuedToken.objects
                   .select_related("user", "client")
                   .filter(token_hash=hash_secret(token),
                           kind=IssuedToken.ACCESS)
                   .first())
            if row is None or row.is_expired():
                return None
            user = row.user
            if not user.is_active or user.status != user.Status.ACTIVE:
                return None
            return AccessToken(
                token=token,
                client_id=row.client.client_id,
                scopes=row.scopes,
                expires_at=int(row.expires_at.timestamp()) if row.expires_at else None,
                resource=_resource(row.resource),
                subject=user.username,
            )

        return await lookup()

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str):
        @sync_to_async
        def lookup():
            row = (IssuedToken.objects
                   .select_related("client", "user")
                   .filter(token_hash=hash_secret(refresh_token),
                           kind=IssuedToken.REFRESH,
                           client__client_id=client.client_id)
                   .first())
            if row is None or row.is_expired():
                return None
            return SdkRefreshToken(
                token=refresh_token,
                client_id=client.client_id,
                scopes=row.scopes,
                expires_at=int(row.expires_at.timestamp()) if row.expires_at else None,
                resource=_resource(row.resource),
                subject=row.user.username,
            )

        return await lookup()

    async def exchange_refresh_token(self, client: OAuthClientInformationFull,
                                     refresh_token, scopes: list[str]) -> OAuthToken:
        access_raw = new_secret()
        new_refresh_raw = new_secret()

        @sync_to_async
        def rotate():
            old = (IssuedToken.objects
                   .select_related("client", "user")
                   .get(token_hash=hash_secret(refresh_token.token),
                        kind=IssuedToken.REFRESH))
            user, client_row = old.user, old.client
            # Narrowing only. A refresh must never be a route to more access
            # than the original consent granted.
            granted = [s for s in (scopes or old.scopes) if s in old.scopes]
            resource = old.resource
            now = timezone.now()
            # Rotation. The old refresh token dies here, and its access tokens
            # cascade with it, so a stolen refresh token is useful once at most
            # and its reuse is a hard failure rather than a silent success.
            old.delete()

            refresh = IssuedToken.objects.create(
                token_hash=hash_secret(new_refresh_raw), kind=IssuedToken.REFRESH,
                client=client_row, user=user, scopes=granted, resource=resource,
                expires_at=now + timedelta(
                    seconds=IssuedToken.REFRESH_LIFETIME_SECONDS),
            )
            IssuedToken.objects.create(
                token_hash=hash_secret(access_raw), kind=IssuedToken.ACCESS,
                client=client_row, user=user, scopes=granted, resource=resource,
                parent=refresh,
                expires_at=now + timedelta(
                    seconds=IssuedToken.ACCESS_LIFETIME_SECONDS),
            )
            return granted

        granted = await rotate()
        return OAuthToken(
            access_token=access_raw,
            token_type="Bearer",
            expires_in=IssuedToken.ACCESS_LIFETIME_SECONDS,
            scope=" ".join(granted),
            refresh_token=new_refresh_raw,
        )

    async def revoke_token(self, token) -> None:
        @sync_to_async
        def delete():
            # Deleting a refresh row cascades to the access rows it minted, so
            # revoking a session revokes it whichever half is presented.
            IssuedToken.objects.filter(token_hash=hash_secret(token.token)).delete()

        await delete()

    async def exchange_identity_assertion(self, client, params) -> OAuthToken:
        # Not supported. Token exchange (RFC 8693) is a separate grant that
        # claude.ai does not use, and AuthSettings.identity_assertion_enabled
        # is left False so the SDK never advertises or routes to it.
        raise NotImplementedError("Identity assertion grant is not enabled.")
