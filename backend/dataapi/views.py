"""
dataapi/views.py
─────────────────
Read-only viewsets for the Data API.

Every view sets authentication_classes and permission_classes LOCALLY. Nothing
here is registered in the global REST_FRAMEWORK settings, so a dapi_ key
reaches exactly these three list/detail endpoints and nothing else.
"""
import logging

from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event

from .authentication import DataApiKeyAuthentication, DataApiKeyUser
from .pagination import DataApiCursorPagination
from .serializers import (
    DataApiBookingSerializer,
    DataApiDelegateSerializer,
    DataApiEventSerializer,
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
