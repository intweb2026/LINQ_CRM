from rest_framework.routers import DefaultRouter

from .views import PerformanceMatrixViewSet

router = DefaultRouter()
router.register(r"", PerformanceMatrixViewSet, basename="performance-matrix")

urlpatterns = router.urls
