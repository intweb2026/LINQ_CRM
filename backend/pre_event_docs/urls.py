from rest_framework.routers import DefaultRouter

from .views import PreEventDocsViewSet

# Registered at the empty prefix, the way event_performance does it, so the
# actions land directly under /api/pre-event-docs/ rather than under a second
# path segment repeating the app name.
router = DefaultRouter()
router.register(r"", PreEventDocsViewSet, basename="pre-event-docs")

urlpatterns = router.urls
