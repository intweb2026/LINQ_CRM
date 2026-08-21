"""
reports/views.py
─────────────────
GET/POST   /api/reports/sources/                     — list / create sheet sources
GET/PATCH  /api/reports/sources/{id}/                — detail / update
DELETE     /api/reports/sources/{id}/                — delete
POST       /api/reports/sources/{id}/detect-columns/ — introspect headers from sheet
POST       /api/reports/sources/list-worksheets/     — list tabs in a spreadsheet URL

That is the whole app now. The definitions, rows, sync-logs and docs endpoints
served the Reports page and went with it, along with the sync and sync-all
actions that wrote the rows nothing reads any more. What is left is the registry
behind the Google Sync page's "Add sheet source": store a connection, and look up
its worksheet names live so the form can offer them.
"""
import logging

from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from accounts.permissions import IsAdminRole
from config.pagination import CachedCountPaginator
from .models import GoogleSheetSource
from .serializers import GoogleSheetSourceSerializer, GoogleSheetSourceListSerializer
from .services.worksheets import WorksheetInspector

logger = logging.getLogger(__name__)


# ── Pagination ────────────────────────────────────────────────────────────────

class StandardPagination(PageNumberPagination):
    page_size            = 100
    page_size_query_param = "page_size"
    max_page_size        = 500
    # Same memoised COUNT(*) as config.pagination.StandardPagination.
    django_paginator_class = CachedCountPaginator


# ── Google Sheet Sources ──────────────────────────────────────────────────────

class GoogleSheetSourceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminRole]
    pagination_class   = StandardPagination

    def get_queryset(self):
        qs = GoogleSheetSource.objects.select_related("created_by")

        if active := self.request.query_params.get("is_active"):
            qs = qs.filter(is_active=active.lower() == "true")
        if sheet_type := self.request.query_params.get("sheet_type"):
            qs = qs.filter(sheet_type=sheet_type)
        if sync_status := self.request.query_params.get("sync_status"):
            qs = qs.filter(sync_status=sync_status)
        if search := self.request.query_params.get("search", "").strip():
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(worksheet_name__icontains=search)
                | Q(description__icontains=search)
                | Q(notes__icontains=search)
            )
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return GoogleSheetSourceListSerializer
        return GoogleSheetSourceSerializer

    # ── Custom actions ─────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="detect-columns")
    def detect_columns(self, request, pk=None):
        """POST /api/reports/sources/{id}/detect-columns/ — fetch column headers live."""
        source = self.get_object()
        result = WorksheetInspector.detect_columns(source)
        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    @action(detail=False, methods=["post"], url_path="list-worksheets")
    def list_worksheets(self, request):
        """POST /api/reports/sources/list-worksheets/ — list tabs for a sheet URL/ID."""
        url = request.data.get("sheet_url") or request.data.get("sheet_id", "")
        if not url:
            return Response({"error": "sheet_url or sheet_id required"}, status=400)
        result = WorksheetInspector.list_worksheets(url)
        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)
