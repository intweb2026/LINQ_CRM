from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import GoogleSheetSourceViewSet

router = DefaultRouter()
router.register(r"sources", GoogleSheetSourceViewSet, basename="report-sources")

urlpatterns = [
    path("", include(router.urls)),
]
