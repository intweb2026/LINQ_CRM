"""
credit_control/urls.py
───────────────────────
    /api/credit-control/leads/              the queue, filtered by ?bucket=
    /api/credit-control/leads/{invoice}/    one lead, and the PATCH that edits it
    /api/credit-control/dashboard/          the aggregate
    /api/credit-control/dispositions/       the dropdown
    /api/credit-control/refresh/            run the routing pass now
    /api/credit-control/run/<job>/          run one scheduled job now, admin only

The three collection-level endpoints are bound explicitly rather than left as
router-generated `leads/dashboard/` paths. They are not leads, and a URL that
says they are would be the first thing anybody reading the frontend has to
un-learn.
"""
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import CreditControlViewSet

router = DefaultRouter()
router.register(r"leads", CreditControlViewSet, basename="credit-control")

urlpatterns = router.urls + [
    path("dashboard/", CreditControlViewSet.as_view({"get": "dashboard"}),
         name="credit-control-dashboard"),
    path("dispositions/", CreditControlViewSet.as_view({"get": "dispositions"}),
         name="credit-control-dispositions"),
    path("refresh/", CreditControlViewSet.as_view({"post": "refresh"}),
         name="credit-control-refresh"),
    # One scheduled job, run by hand. Bound here rather than left as the
    # router's `leads/run/<job>/`, because it is not a lead.
    path("run/<str:job>/", CreditControlViewSet.as_view({"post": "run_job"}),
         name="credit-control-run-job"),
]
