"""
Linq CRM — Root URL configuration
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.generic import TemplateView
from rest_framework.routers import DefaultRouter

from accounts.views import UserViewSet, GoogleTokenLoginView, CustomAuthToken
from companies.views import CompanyViewSet
from events.views import EventViewSet
from book_event.views import BookEventViewSet
from book_delegate.views import BookDelegateViewSet
from teams.views import TeamViewSet as TeamManagementViewSet
from ticket_central.views import TicketViewSet
from paper_review.views import PaperReviewViewSet
from proposal_submission.views import ProposalSubmissionViewSet
from config.views import GlobalSearchView, DashboardStatsView, DashboardAggregateView

router = DefaultRouter()
router.register(r"users",     UserViewSet,         basename="users")
# /api/roles/ is gone. The team IS the role now, so its permission grid lives at
# /api/teams/{id}/permissions/ and a person's exceptions at
# /api/users/{id}/permissions/.
router.register(r"teams",     TeamManagementViewSet, basename="teams")
router.register(r"companies", CompanyViewSet,      basename="companies")
router.register(r"events",    EventViewSet,        basename="events")
router.register(r"invoices",  BookEventViewSet,    basename="invoices")
router.register(r"delegates", BookDelegateViewSet, basename="delegates")
router.register(r"tickets",   TicketViewSet,       basename="tickets")
router.register(r"proposal-submissions", ProposalSubmissionViewSet,
                basename="proposal-submissions")
# The path frontend/src/api/paperReview.js was written against.
router.register(r"paper-reviews", PaperReviewViewSet, basename="paper-reviews")

urlpatterns = [
    path("admin/",               admin.site.urls),
    path("api/",                 include(router.urls)),
    path("api/webhooks/",        include("webhooks.urls")),
    path("api/google-sync/",     include("google_sync.urls")),
    path("api/reports/",         include("reports.urls")),
    # Read-only export surface. Authenticated by X-DATA-API-KEY only — see
    # dataapi/authentication.py. Kept off the router above because it is a
    # separate credential domain, not part of the session-authenticated API.
    path("api/data/",            include("dataapi.urls")),
    path("api/event-performance/", include("event_performance.urls")),
    path("api/historical-events/", include("historical_event_registry.urls")),
    path("api/search/",          GlobalSearchView.as_view(),    name="global-search"),
    path("api/stats/dashboard/", DashboardStatsView.as_view(), name="dashboard-stats"),
    # GROUP BY aggregates for the Dashboard. Replaces ~350 sequential
    # fetchAllPages requests the browser used to make to compute these.
    path("api/stats/dashboard_aggregate/", DashboardAggregateView.as_view(),
         name="dashboard-aggregate"),
    # Google Sign-In is the only login method. This must stay ahead of the React
    # catch-all, which swallows anything it is allowed to see.
    path("api/auth/google/",   GoogleTokenLoginView.as_view(), name="google-login"),
    # Hidden break-glass fallback, reachable only via the /170405 front-end
    # gate. Unlike the Google path it does not check login_access.
    path("api/auth/fallback/", CustomAuthToken.as_view(),      name="fallback-login"),
    path("api-auth/",            include("rest_framework.urls")),
    # Serve React frontend for all non-API routes
    re_path(r"^(?!api/|admin/|api-auth/|static/).*$",
            TemplateView.as_view(template_name="index.html"),
            name="react-frontend"),
]
