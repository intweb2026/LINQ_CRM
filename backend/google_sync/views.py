"""
google_sync/views.py
─────────────────────
GET    /api/google-sync/logs/            — paginated sync history
GET    /api/google-sync/logs/{id}/       — single log detail
DELETE /api/google-sync/logs/{id}/       — delete one log from the history
GET    /api/google-sync/status/          — live dashboard summary
POST   /api/google-sync/run/             — trigger manual sync
POST   /api/google-sync/retry/{id}/      — retry a failed sync
GET    /api/google-sync/catalog/         — modules and columns a target may pick
       /api/google-sync/targets/         — CRUD for user-defined pushes
POST   /api/google-sync/targets/{id}/run/ — run one target now

ACCESS: crm_permission("google_sync"), not IsAdminRole
This app used to be admin-only, which meant Google Sync could not be granted to
a team however the grid was filled in, and it shared the "webhooks" cell with a
page it has nothing to do with. It now has its own module in CRM_MODULES, so
access is a grant like every other page — admins and all-access teams still get
in, because their matrix is all-True by construction.

WHICH CELL EACH ACTION READS
crm_permission maps DRF action names first and the HTTP method second. Nothing
here is in its action sets, so every read is `view`, every POST — running a sync,
retrying one, pushing a target — is `create`, and DELETE is `delete`. `create` is
the honest reading of a sync run: it writes a new GoogleSheetSyncLog and a new
tab body, and it destroys nothing in the CRM.
"""
import logging
from django.db.models import Q
from rest_framework import mixins, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.crm_permissions import crm_permission
from book_event.models import SyncLog
from sync import catalog
from .models import GoogleSheetSyncLog, SheetSyncTarget
from .serializers import GoogleSheetSyncLogSerializer, SheetSyncTargetSerializer
from .services import SyncOrchestrator

logger = logging.getLogger("book_event")

VALID_SYNC_TYPES = {"bookings", "events", "full_sync", "crm_mirror"}


class GoogleSyncLogViewSet(mixins.DestroyModelMixin,
                           viewsets.ReadOnlyModelViewSet):
    """
    List, read and DELETE sync history.

    Read-only until the history grew a delete button. Delete is the whole reason
    this is not a plain ReadOnlyModelViewSet: a failed run leaves a row that
    stays at the top of the page forever once it has been read and acted on, and
    prune_logs only ages rows out on a schedule measured in months.

    Deleting the log of a RUNNING sync is refused. The row is not history yet —
    /status/ reads it as `running_log` to tell the page a job is in flight, and
    removing it mid-run would report the sync as finished while it is still
    writing to the sheet.
    """
    serializer_class   = GoogleSheetSyncLogSerializer
    permission_classes = [crm_permission("google_sync")]

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

    def destroy(self, request, *args, **kwargs):
        log = self.get_object()
        if log.status == "running":
            return Response(
                {"error": "This sync is still running. Wait for it to finish before deleting its log."},
                status=status.HTTP_409_CONFLICT,
            )
        logger.info("Sync log #%s deleted by %s", log.pk, request.user.username)
        return super().destroy(request, *args, **kwargs)


class GoogleSyncStatusView(APIView):
    """
    GET /api/google-sync/status/
    Returns live summary: last sync per dataset, is_running flag, latest log.
    """
    permission_classes = [crm_permission("google_sync")]

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
    permission_classes = [crm_permission("google_sync")]

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
    permission_classes = [crm_permission("google_sync")]

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


class SyncCatalogView(APIView):
    """
    GET /api/google-sync/catalog/

    Every module a target may be pointed at, with its selectable columns. The
    picker is drawn from this, so it cannot offer a column the runner would
    reject.
    """
    permission_classes = [crm_permission("google_sync")]

    def get(self, request):
        return Response({"modules": catalog.list_modules()})


class SheetSyncTargetViewSet(viewsets.ModelViewSet):
    """
    CRUD for user-defined pushes, plus POST {id}/run/ to run one now.
    """
    serializer_class   = SheetSyncTargetSerializer
    permission_classes = [crm_permission("google_sync")]

    def get_queryset(self):
        qs = SheetSyncTarget.objects.select_related("created_by").all()

        module = self.request.query_params.get("module")
        if module:
            qs = qs.filter(module=module)

        enabled = self.request.query_params.get("is_enabled")
        if enabled in ("true", "false"):
            qs = qs.filter(is_enabled=enabled == "true")

        search = self.request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(tab_name__icontains=search)
            )

        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        target = self.get_object()

        if not target.is_enabled:
            return Response(
                {"error": "This target is disabled. Enable it before running."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            log = SyncOrchestrator.run_target(
                target,
                triggered_by=request.user.username,
                trigger_source=GoogleSheetSyncLog.TriggerSource.ADMIN_MANUAL,
            )
        except RuntimeError as exc:
            # Another sync holds the lock.
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)

        payload = {
            "log":    GoogleSheetSyncLogSerializer(log).data,
            "target": SheetSyncTargetSerializer(target).data,
        }
        resp_status = (
            status.HTTP_200_OK if log.status == "success"
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        return Response(payload, status=resp_status)
