"""
Consent page routes.

Mounted under /api/ in config/urls.py, and that prefix is load bearing rather
than tidy. In production the React build is served by a separate Node process
which forwards only a fixed list of prefixes to Django; a path outside that
list is answered with the SPA shell. See frontend/scripts/serve-build.mjs.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("consent/", views.consent, name="mcp-auth-consent"),
    path("consent/complete/", views.complete, name="mcp-auth-consent-complete"),
]
