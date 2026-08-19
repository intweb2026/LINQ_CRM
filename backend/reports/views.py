"""
reports/views.py
─────────────────
GET/POST   /api/reports/sources/                     — list / create sheet sources
GET/PATCH  /api/reports/sources/{id}/                — detail / update
DELETE     /api/reports/sources/{id}/                — delete
POST       /api/reports/sources/{id}/sync/           — trigger single-source sync
POST       /api/reports/sources/sync-all/            — sync all active sources
GET        /api/reports/sources/{id}/rows/           — paginated rows for a source
GET        /api/reports/sources/{id}/preview/        — first 20 rows (no pagination)
POST       /api/reports/sources/{id}/detect-columns/ — introspect headers from sheet
POST       /api/reports/list-worksheets/             — list tabs in a spreadsheet URL

GET/POST   /api/reports/definitions/                 — list / create report definitions
GET/PATCH  /api/reports/definitions/{id}/            — detail / update

GET        /api/reports/rows/                        — query rows across all sources
GET        /api/reports/sync-logs/                   — sync log list (filterable by source)
GET        /api/reports/sync-logs/{id}/              — log detail

GET        /api/reports/docs/                        — list .md documentation files
GET        /api/reports/docs/{filename}/             — serve markdown file content
"""
import os
import logging
from pathlib import Path

from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminRole
from config.pagination import CachedCountPaginator
from .models import GoogleSheetSource, ReportDefinition, ReportRow, ReportSyncLog
from .serializers import (
    GoogleSheetSourceSerializer, GoogleSheetSourceListSerializer,
    ReportDefinitionSerializer,
    ReportRowSerializer, ReportRowListSerializer,
    ReportSyncLogSerializer,
)
from .services.sync import ReportSyncOrchestrator

logger = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).resolve().parent / "docs"


# ── Pagination ────────────────────────────────────────────────────────────────

class StandardPagination(PageNumberPagination):
    page_size            = 100
    page_size_query_param = "page_size"
    max_page_size        = 500
    # Same memoised COUNT(*) as config.pagination.StandardPagination. This class
    # is a separate subclass only because reports pages 100 rows at a time rather
    # than 50; the counting behaviour has no reason to differ, and ReportRow is
    # the largest table any list endpoint reads.
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

    @action(detail=True, methods=["post"])
    def sync(self, request, pk=None):
        """POST /api/reports/sources/{id}/sync/"""
        source = self.get_object()
        log = ReportSyncOrchestrator.sync_source(
            source,
            triggered_by=request.user.username,
            trigger_source=ReportSyncLog.TriggerSource.MANUAL,
        )
        return Response(ReportSyncLogSerializer(log).data)

    @action(detail=False, methods=["post"], url_path="sync-all")
    def sync_all(self, request):
        """POST /api/reports/sources/sync-all/"""
        logs = ReportSyncOrchestrator.sync_all(
            triggered_by=request.user.username,
            trigger_source=ReportSyncLog.TriggerSource.MANUAL,
        )
        success = sum(1 for l in logs if l.status == ReportSyncLog.Status.SUCCESS)
        failed  = sum(1 for l in logs if l.status == ReportSyncLog.Status.FAILED)
        return Response({
            "synced":   len(logs),
            "success":  success,
            "failed":   failed,
            "log_ids":  [l.id for l in logs],
        })

    @action(detail=True, methods=["get"])
    def rows(self, request, pk=None):
        """GET /api/reports/sources/{id}/rows/ — paginated data rows."""
        source = self.get_object()
        qs = ReportRow.objects.filter(source=source, is_active=True)

        if search := request.query_params.get("search", "").strip():
            qs = qs.filter(
                Q(processed_data__icontains=search) | Q(raw_data__icontains=search)
            )

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(ReportRowListSerializer(page, many=True).data)
        return Response(ReportRowListSerializer(qs, many=True).data)

    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        """GET /api/reports/sources/{id}/preview/ — first 20 rows."""
        source = self.get_object()
        rows   = ReportRow.objects.filter(source=source, is_active=True)[:20]
        return Response(ReportRowListSerializer(rows, many=True).data)

    @action(detail=True, methods=["post"], url_path="detect-columns")
    def detect_columns(self, request, pk=None):
        """POST /api/reports/sources/{id}/detect-columns/ — fetch column headers live."""
        source = self.get_object()
        result = ReportSyncOrchestrator.detect_columns(source)
        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    @action(detail=False, methods=["post"], url_path="list-worksheets")
    def list_worksheets(self, request):
        """POST /api/reports/sources/list-worksheets/ — list tabs for a sheet URL/ID."""
        url = request.data.get("sheet_url") or request.data.get("sheet_id", "")
        if not url:
            return Response({"error": "sheet_url or sheet_id required"}, status=400)
        result = ReportSyncOrchestrator.list_worksheets(url)
        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


# ── Report Definitions ────────────────────────────────────────────────────────

class ReportDefinitionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminRole]
    serializer_class   = ReportDefinitionSerializer

    def get_queryset(self):
        qs = ReportDefinition.objects.prefetch_related("sources").select_related("created_by")
        if search := self.request.query_params.get("search", "").strip():
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))
        if active := self.request.query_params.get("is_active"):
            qs = qs.filter(is_active=active.lower() == "true")
        return qs


# ── Report Rows ───────────────────────────────────────────────────────────────

class ReportRowViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdminRole]
    pagination_class   = StandardPagination
    serializer_class   = ReportRowListSerializer

    def get_queryset(self):
        qs = ReportRow.objects.filter(is_active=True).select_related("source")
        if source_id := self.request.query_params.get("source"):
            qs = qs.filter(source_id=source_id)
        if search := self.request.query_params.get("search", "").strip():
            qs = qs.filter(
                Q(processed_data__icontains=search) | Q(raw_data__icontains=search)
            )
        return qs


# ── Sync Logs ─────────────────────────────────────────────────────────────────

class ReportSyncLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdminRole]
    serializer_class   = ReportSyncLogSerializer
    pagination_class   = StandardPagination

    def get_queryset(self):
        qs = ReportSyncLog.objects.select_related("source")
        if source_id := self.request.query_params.get("source"):
            qs = qs.filter(source_id=source_id)
        if st := self.request.query_params.get("status"):
            qs = qs.filter(status=st)
        return qs


# ── Documentation files ───────────────────────────────────────────────────────

class ReportDocsListView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        if not DOCS_DIR.exists():
            return Response([])
        files = sorted(
            [
                {"filename": f.name, "size": f.stat().st_size}
                for f in DOCS_DIR.glob("*.md")
            ],
            key=lambda x: x["filename"],
        )
        return Response(files)


class ReportDocDetailView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request, filename):
        safe = os.path.basename(filename)
        if not safe.endswith(".md"):
            return Response({"error": "Only .md files are served here"}, status=400)
        path = DOCS_DIR / safe
        if not path.exists():
            return Response({"error": "File not found"}, status=404)
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            return Response({"error": str(exc)}, status=500)
        return Response({"filename": safe, "content": content})
