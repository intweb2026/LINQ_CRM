from django.urls import path

from .views import (
    GmailCallbackView, GmailConnectView, GmailDisconnectView, GmailStatusView,
)

urlpatterns = [
    path("status/", GmailStatusView.as_view(), name="gmail-status"),
    path("connect/", GmailConnectView.as_view(), name="gmail-connect"),
    path("callback/", GmailCallbackView.as_view(), name="gmail-callback"),
    path("disconnect/", GmailDisconnectView.as_view(), name="gmail-disconnect"),
]
