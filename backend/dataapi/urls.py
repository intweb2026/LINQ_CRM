from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import BookingDataViewSet, DelegateDataViewSet, EventDataViewSet

router = DefaultRouter()
router.register(r"bookings",  BookingDataViewSet,  basename="dataapi-bookings")
router.register(r"delegates", DelegateDataViewSet, basename="dataapi-delegates")
router.register(r"events",    EventDataViewSet,    basename="dataapi-events")

urlpatterns = [
    path("", include(router.urls)),
]
