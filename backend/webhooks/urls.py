from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (WebhookIngestionView, TicketIngestionView,
                    PaperReviewIngestionView,
                    WebhookLogViewSet, WebhookApiKeyViewSet)

router = DefaultRouter()
router.register(r"logs",  WebhookLogViewSet,    basename="webhook-logs")
router.register(r"keys",  WebhookApiKeyViewSet, basename="webhook-keys")

urlpatterns = [
    path("ingest/",   WebhookIngestionView.as_view(), name="webhook-ingest"),
    path("bookings/", WebhookIngestionView.as_view(), name="webhook-ingest-legacy"),
    path("paper-review/", PaperReviewIngestionView.as_view(), name="webhook-ingest-paper-review"),
    path("tickets/",  TicketIngestionView.as_view(),  name="webhook-ingest-tickets"),
    path("",          include(router.urls)),
]
