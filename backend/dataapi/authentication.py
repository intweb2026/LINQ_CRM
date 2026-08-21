"""
dataapi/authentication.py
──────────────────────────
X-DATA-API-KEY authentication for the Data API.

CRITICAL: DataApiKeyAuthentication must NEVER appear in
settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]. It is set per-view,
in dataapi/views.py only. Adding it globally would let a read-only export key
authenticate against every CRM endpoint, writes included.
"""
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import DataApiKey


class DataApiKeyUser:
    """
    Sentinel request.user for Data API key authenticated requests.

    Carries no Django user identity, and deliberately so: it is not a User
    instance, so any view guarded by a permission class that inspects a real
    account — RBAC scoping, role checks, effective_permissions — rejects it.
    Only DataApiPermission accepts it.
    """
    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_admin = False
    is_staff = False
    is_superuser = False
    role = "data_api"
    username = "data_api"
    id = None
    pk = None

    def __init__(self, api_key_obj):
        self.api_key = api_key_obj
        self.username = f"data_api:{api_key_obj.name}"

    def __str__(self):
        return self.username


class DataApiKeyAuthentication(BaseAuthentication):
    """
    Authenticate via the X-DATA-API-KEY header.
    Set per-view only — never in global REST_FRAMEWORK settings.
    """
    HEADER = "HTTP_X_DATA_API_KEY"

    def authenticate(self, request):
        raw_key = request.META.get(self.HEADER, "").strip()
        if not raw_key:
            # No header at all: return None so DRF falls through to the
            # permission check, which answers 401 via authenticate_header.
            return None

        if not raw_key.startswith(DataApiKey.PREFIX):
            raise AuthenticationFailed("Invalid API key format.")

        key_hash = DataApiKey.hash_key(raw_key)
        try:
            api_key = DataApiKey.objects.get(key_hash=key_hash)
        except DataApiKey.DoesNotExist:
            raise AuthenticationFailed("Invalid API key.")

        if not api_key.is_valid():
            if not api_key.is_active:
                raise AuthenticationFailed("This API key has been deactivated.")
            raise AuthenticationFailed("This API key has expired.")

        api_key.record_usage()
        return (DataApiKeyUser(api_key), raw_key)

    def authenticate_header(self, request):
        return "X-DATA-API-KEY"
