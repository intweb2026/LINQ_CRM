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
        # Reset before every build. _apply_param_filter appends to this, and the
        # request log reads it afterwards, so it has to describe THIS call only.
        self._applied_filters = []
        return self._base_queryset()

    def _base_queryset(self):
        raise NotImplementedError

    def _apply_param_filter(self, qs, param, field, lookup="exact"):
        """
        Apply one query-param filter and record what was applied.

        Behaviourally identical to the guarded `qs.filter(field=value)` each
        subclass wrote inline: same truthiness check, and `field__exact=v` is
        the same ORM node Django builds for `field=v`. The only thing added is
        the note kept for the request log, which is taken FROM the applied
        filter rather than written alongside it, so the log cannot drift from
        what the queryset actually did.
        """
        value = self.request.query_params.get(param)
        if not value:
            return qs
        self._applied_filters.append(f"{field}[{lookup}]={value!r}")
        return qs.filter(**{f"{field}__{lookup}": value})

    def _log_request(self, request, rows):
        """
        One INFO line per call to this surface, at dataapi.views.

        Logged after the page is built, because `rows` is the size of the page
        actually serialised, not the size of the queryset. `query` is the raw
        QUERY_STRING as received, unparsed and undecoded, so a param the view
        ignored is still visible in the log; `filters` is the list the ORM
        really got. The two disagreeing is the point of logging both.
        """
        api_key = getattr(request.user, "api_key", None)
        logger.info(
            'dataapi resource=%s method=%s path=%s query="%s" filters=[%s] rows=%d key=%s',
            self.resource_name,
            request.method,
            request.path,
            request.META.get("QUERY_STRING", ""),
            "; ".join(getattr(self, "_applied_filters", [])),
            rows,
            getattr(api_key, "key_preview", "-") or "-",
        )

    def list(self, request, *args, **kwargs):
        """Wrap the paginated page in an envelope the GAS client can read."""
        response = super().list(request, *args, **kwargs)
        data = response.data
        results = data.get("results", data) if isinstance(data, dict) else data
        self._log_request(request, len(results) if results is not None else 0)
        return Response({
            "resource": self.resource_name,
            "results": results,
            "next": data.get("next") if isinstance(data, dict) else None,
            "previous": data.get("previous") if isinstance(data, dict) else None,
        })

    def retrieve(self, request, *args, **kwargs):
        """Detail reads are logged too; a single row is still a page of one."""
        response = super().retrieve(request, *args, **kwargs)
        self._log_request(request, 1)
        return response


class BookingDataViewSet(DataApiBaseViewSet):
    resource_name = "bookings"
    serializer_class = DataApiBookingSerializer

    def _base_queryset(self):
        qs = BookEvent.objects.select_related("sales_executive", "team_leader").order_by("pk")
        qs = self._apply_param_filter(qs, "event_code", "event_code")
        qs = self._apply_param_filter(qs, "updated_since", "updated_at", "gte")
        return qs


class DelegateDataViewSet(DataApiBaseViewSet):
    resource_name = "delegates"
    serializer_class = DataApiDelegateSerializer

    def _base_queryset(self):
        # invoice__sales_executive is joined here and not left to the serializer:
        # DataApiDelegateSerializer reports the invoice's sales executive by
        # name, and without the join that is one extra query per delegate on a
        # 500-row page. See test_delegate_page_query_count_is_independent_of_row_count.
        qs = (
            BookDelegate.objects
            .select_related("invoice", "invoice__sales_executive", "company")
            .order_by("pk")
        )
        qs = self._apply_param_filter(qs, "event_code", "event_code")
        qs = self._apply_param_filter(qs, "updated_since", "updated_at", "gte")
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
        qs = self._apply_param_filter(qs, "event_code", "event_code")
        qs = self._apply_param_filter(qs, "updated_since", "updated_at", "gte")
        # NOTE: the inline version bound this to a local named `status`, which
        # shadowed the imported rest_framework.status module for the rest of the
        # method. Nothing below it used the module, so it was harmless; routing
        # it through the helper removes the shadow as a side effect.
        qs = self._apply_param_filter(qs, "status", "status")
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
