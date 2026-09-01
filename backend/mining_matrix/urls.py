from rest_framework.routers import DefaultRouter

from .views import MiningMatrixViewSet

router = DefaultRouter()
router.register(r"", MiningMatrixViewSet, basename="mining-matrix")

urlpatterns = router.urls
