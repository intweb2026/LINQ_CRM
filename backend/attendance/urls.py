from rest_framework.routers import DefaultRouter

from .views import AttendanceViewSet

# Registered at the empty prefix, the way pre_event_docs does it, so the log is
# /api/attendance/ and the actions land directly under it rather than under a
# second path segment repeating the app name.
router = DefaultRouter()
router.register(r"", AttendanceViewSet, basename="attendance")

urlpatterns = router.urls
