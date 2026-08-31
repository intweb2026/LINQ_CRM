from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BookingDataViewSet,
    DelegateDataViewSet,
    DeletionDataViewSet,
    DataApiKeyManagementViewSet,
    EventDataViewSet,
    TicketDataViewSet,
)

router = DefaultRouter()
router.register(r"bookings",  BookingDataViewSet,  basename="dataapi-bookings")
router.register(r"delegates", DelegateDataViewSet, basename="dataapi-delegates")
router.register(r"events",    EventDataViewSet,    basename="dataapi-events")
router.register(r"tickets",   TicketDataViewSet,   basename="dataapi-tickets")
# Not a fifth scope: "deletions" is absent from DATA_API_SCOPES on purpose and
# the view checks the key against the ?resource= it was asked about instead.
router.register(r"deletions", DeletionDataViewSet, basename="dataapi-deletions")

# Key management is NOT an export resource, and it is deliberately OFF the
# router. The router is the export surface: every prefix registered above is a
# resource a dapi_ key can be scoped to, and a "keys" registration sitting in
# that list reads like a fifth one. These two paths are explicit instead, so
# the split between the two surfaces is visible here rather than only in the
# view. Session/Token auth, HP account only; see DataApiKeyManagementViewSet.
key_management = DataApiKeyManagementViewSet.as_view({
    "get": "list",
    "post": "create",
})
key_revoke = DataApiKeyManagementViewSet.as_view({
    "post": "revoke",
})

urlpatterns = [
    path("", include(router.urls)),
    path("keys/", key_management, name="dataapi-keys-list"),
    path("keys/<int:pk>/revoke/", key_revoke, name="dataapi-keys-revoke"),
]
