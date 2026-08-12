"""
google_sync/views.py
─────────────────────
GET  /api/google-sync/logs/         — paginated sync history (admin only)
GET  /api/google-sync/logs/{id}/    — single log detail
GET  /api/google-sync/status/       — live dashboard summary
POST /api/google-sync/run/          — trigger manual sync
POST /api/google-sync/retry/{id}/   — retry a failed sync
"""
import logging
from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminRole
from book_event.models import SyncLog
from .models import GoogleSheetSyncLog
from .serializers import GoogleSheetSyncLogSerializer
from .services import SyncOrchestrator

logger = logging.getLogger("book_event")

VALID_SYNC_TYPES = {"bookings", "events", "full_sync", "crm_mirror"}


class GoogleSyncLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class   = GoogleSheetSyncLogSerializer
    permission_classes = [IsAdminRole]

    def get_queryset(self):
        qs = GoogleSheetSyncLog.objects.all()

        st = self.request.query_params.get("status")
        if st:
            qs = qs.filter(status=st)

        sync_type = self.request.query_params.get("sync_type")
        if sync_type:
            qs = qs.filter(sync_type=sync_type)

        trigger = self.request.query_params.get("trigger_source")
        if trigger:
            qs = qs.filter(trigger_source=trigger)

        search = self.request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(triggered_by__icontains=search) |
                Q(sheet_name__icontains=search)   |
                Q(error_message__icontains=search)
            )

        return qs


class GoogleSyncStatusView(APIView):
    """
    GET /api/google-sync/status/
    Returns live summary: last sync per dataset, is_running flag, latest log.
    """
    permission_classes = [IsAdminRole]

    def get(self, request):
        result = {"is_running": SyncOrchestrator.is_running()}

        for dataset in ("bookings", "events"):
            sl = SyncLog.objects.filter(dataset=dataset).first()
            result[dataset] = {
                "last_synced_at":  sl.last_synced_at  if sl else None,
                "last_status":     sl.last_status     if sl else None,
                "records_synced":  sl.records_synced  if sl else 0,
                "error_message":   sl.error_message   if sl else "",
            }

        latest = GoogleSheetSyncLog.objects.first()
        result["latest_log"] = GoogleSheetSyncLogSerializer(latest).data if latest else None

        running_log = GoogleSheetSyncLog.objects.filter(status="running").order_by("-started_at").first()
        result["running_log"] = GoogleSheetSyncLogSerializer(running_log).data if running_log else None

        return Response(result)


class GoogleSyncRunView(APIView):
    """
    POST /api/google-sync/run/
    Body: { "sync_type": "bookings"|"events"|"full_sync", "full": false }
    """
    permission_classes = [IsAdminRole]

    def post(self, request):
        sync_type = request.data.get("sync_type", "full_sync")
        full      = bool(request.data.get("full", False))

        if sync_type not in VALID_SYNC_TYPES:
            return Response(
                {"error": f"Invalid sync_type. Valid options: {', '.join(sorted(VALID_SYNC_TYPES))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            log = SyncOrchestrator.run(
                sync_type=sync_type,
                full=full,
                triggered_by=request.user.username,
                trigger_source=GoogleSheetSyncLog.TriggerSource.ADMIN_MANUAL,
            )
        except RuntimeError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)

        resp_status = (
            status.HTTP_200_OK if log.status == "success"
            else status.HTTP_207_MULTI_STATUS if log.status == "partial_success"
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        return Response(GoogleSheetSyncLogSerializer(log).data, status=resp_status)


class GoogleSyncRetryView(APIView):
    """
    POST /api/google-sync/retry/{id}/
    Re-runs the same sync type as the original log.
    """
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        try:
            original = GoogleSheetSyncLog.objects.get(pk=pk)
        except GoogleSheetSyncLog.DoesNotExist:
            return Response({"error": "Sync log not found."}, status=status.HTTP_404_NOT_FOUND)

        if original.status == "success":
            return Response(
                {"error": "This sync already succeeded. Retry is only available for failed or partial syncs."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        full = original.sync_mode == "full"

        try:
            log = SyncOrchestrator.run(
                sync_type=original.sync_type,
                full=full,
                triggered_by=f"{request.user.username} (retry #{original.id})",
                trigger_source=GoogleSheetSyncLog.TriggerSource.ADMIN_MANUAL,
            )
        except RuntimeError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)

        resp_status = (
            status.HTTP_200_OK if log.status == "success"
            else status.HTTP_207_MULTI_STATUS if log.status == "partial_success"
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        return Response(GoogleSheetSyncLogSerializer(log).data, status=resp_status)
