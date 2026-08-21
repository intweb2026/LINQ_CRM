"""
dataapi/views.py
─────────────────
Read-only viewsets for the Data API.

Every view sets authentication_classes and permission_classes LOCALLY. Nothing
here is registered in the global REST_FRAMEWORK settings, so a dapi_ key
reaches exactly these four list/detail endpoints and nothing else.
"""
import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from accounts.permissions import IsHPAccount
from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from dataapi.models import DataApiKey
from events.models import Event
from ticket_central.models import Ticket

from .authentication import DataApiKeyAuthentication, DataApiKeyUser
from .pagination import DataApiCursorPagination
from .serializers import (
    DataApiBookingSerializer,
    DataApiDelegateSerializer,
    DataApiEventSerializer,
    DataApiKeyCreateSerializer,
    DataApiKeyListSerializer,
    DataApiTicketSerializer,
)

logger = logging.getLogger(__name__)


class DataApiPermission(BasePermission):
    """
    Only a DataApiKeyUser gets through.

    Deliberately not IsAuthenticated: a logged-in CRM session must not reach
    these endpoints either. The Data API is one credential type, one surface.
    """

    def has_permission(self, request, view):
        return isinstance(request.user, DataApiKeyUser)


class DataApiBaseViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Base for all Data API viewsets.
    - Authentication: X-DATA-API-KEY only, per-view, never global
    - Pagination: keyset/cursor on pk
    - Scope check: the key must include this resource in its scopes
    """
    authentication_classes = [DataApiKeyAuthentication]
    permission_classes = [DataApiPermission]
    pagination_class = DataApiCursorPagination
    # Emptied on purpose. The project default includes StableOrderingFilter, and
    # CursorPagination hands ordering to any OrderingFilter it finds on the view;
    # a client-supplied ?ordering= would then invalidate every cursor already
    # issued. The Data API exposes filtering through explicit query params below.
    filter_backends = []
    resource_name = ""  # Override in subclass

    def get_queryset(self):
        api_key = getattr(self.request.user, "api_key", None)
        if api_key and not api_key.has_scope(self.resource_name):
            raise PermissionDenied(
                f"This API key does not have access to the '{self.resource_name}' resource."
            )
        return self._base_queryset()

    def _base_queryset(self):
        raise NotImplementedError

    def list(self, request, *args, **kwargs):
        """Wrap the paginated page in an envelope the GAS client can read."""
        response = super().list(request, *args, **kwargs)
        data = response.data
        return Response({
            "resource": self.resource_name,
            "results": data.get("results", data) if isinstance(data, dict) else data,
            "next": data.get("next") if isinstance(data, dict) else None,
            "previous": data.get("previous") if isinstance(data, dict) else None,
        })


class BookingDataViewSet(DataApiBaseViewSet):
    resource_name = "bookings"
    serializer_class = DataApiBookingSerializer

    def _base_queryset(self):
        qs = BookEvent.objects.select_related("sales_executive", "team_leader").order_by("pk")
        event_code = self.request.query_params.get("event_code")
        if event_code:
            qs = qs.filter(event_code=event_code)
        updated_since = self.request.query_params.get("updated_since")
        if updated_since:
            qs = qs.filter(updated_at__gte=updated_since)
        return qs


class DelegateDataViewSet(DataApiBaseViewSet):
    resource_name = "delegates"
    serializer_class = DataApiDelegateSerializer

    def _base_queryset(self):
        qs = BookDelegate.objects.select_related("invoice", "company").order_by("pk")
        event_code = self.request.query_params.get("event_code")
        if event_code:
            qs = qs.filter(event_code=event_code)
        updated_since = self.request.query_params.get("updated_since")
        if updated_since:
            qs = qs.filter(updated_at__gte=updated_since)
        return qs


class EventDataViewSet(DataApiBaseViewSet):
    resource_name = "events"
    serializer_class = DataApiEventSerializer

    def _base_queryset(self):
        return Event.objects.order_by("pk")


class TicketDataViewSet(DataApiBaseViewSet):
    resource_name = "tickets"
    serializer_class = DataApiTicketSerializer

    def _base_queryset(self):
        qs = (
            Ticket.objects
            .select_related("created_by", "mr_submitted_by", "dmd_submitted_by", "returned_by")
            .order_by("pk")
        )
        event_code = self.request.query_params.get("event_code")
        if event_code:
            qs = qs.filter(event_code=event_code)
        updated_since = self.request.query_params.get("updated_since")
        if updated_since:
            qs = qs.filter(updated_at__gte=updated_since)
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        return qs


class DataApiKeyManagementViewSet(viewsets.ViewSet):
    """
    HP-only key management for the CRM UI, at /api/data/keys/.

    AUTHENTICATION IS DELIBERATELY NOT SET HERE. Leaving
    authentication_classes unset means this view inherits the project default,
    Token/Session, exactly like every other CRM view. That is the whole point of
    the separation: the four export viewsets above pin
    DataApiKeyAuthentication locally, so a dapi_ key reaches data and nothing
    else, and cannot mint, list, or revoke keys. Naming
    DataApiKeyAuthentication here would let a leaked export key issue itself new
    ones.

    It is a plain ViewSet rather than a ModelViewSet because there is no update
    path and no destroy path. A key is created once and revoked; editing a
    key's scopes in place would silently widen a credential already deployed in
    somebody's Apps Script, and deleting the row would destroy the usage
    history that says what that credential did.

    AUDIENCE IS ONE ACCOUNT, NOT A ROLE. This was IsAdminRole, which admits
    three kinds of caller: role == admin, a team flagged is_all_access, and HP.
    Every one of those is a legitimate administrator who must nevertheless NOT
    see this surface, because a key minted here reads the whole export API and
    is shown in the clear exactly once; listing is as sensitive as creating,
    since the rows name what each live credential can reach. IsHPAccount is the
    same primitive the clear-all endpoints use, so "restricted to one named
    account" has one definition in this codebase rather than two.
    """
    permission_classes = [IsHPAccount]

    def list(self, request):
        qs = DataApiKey.objects.select_related("created_by").order_by("-created_at")
        return Response(DataApiKeyListSerializer(qs, many=True).data)

    def create(self, request):
        ser = DataApiKeyCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        # DataApiKey.create_key() generates the raw key, hashes it, and builds
        # key_preview. Hashing inline here would be a second implementation of
        # the same secret handling, free to drift from the authenticator that
        # verifies it; the management command already goes through this path.
        key_obj, raw_key = DataApiKey.create_key(
            name=ser.validated_data["name"],
            scopes=ser.validated_data["scopes"],
            expires_at=ser.validated_data.get("expires_at"),
            created_by=request.user,
        )

        # raw_key appears in this response and never again. It is not stored,
        # and no other endpoint can reconstruct it from key_hash.
        return Response(
            {
                "id": key_obj.id,
                "name": key_obj.name,
                "key_preview": key_obj.key_preview,
                "scopes": key_obj.scopes,
                "raw_key": raw_key,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        """POST /api/data/keys/{id}/revoke/ — one-way; is_active never comes back."""
        try:
            key_obj = DataApiKey.objects.get(pk=pk)
        except DataApiKey.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        key_obj.is_active = False
        key_obj.save(update_fields=["is_active"])
        logger.info("Data API key %s (%s) revoked by %s",
                    key_obj.pk, key_obj.key_preview, request.user)
        return Response({"id": key_obj.id, "is_active": False})
