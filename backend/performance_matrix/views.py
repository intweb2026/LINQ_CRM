"""
performance_matrix/views.py
────────────────────────────
GET   /api/performance-matrix/                the matrix, `upcoming` view
GET   /api/performance-matrix/?view=all       past editions included
PATCH /api/performance-matrix/{event id}/verdict/   {"verdict": "Going Ahead"}

ADMIN ONLY, on every action. The figures are live paid and pending heads across
the whole catalogue, the same commercial numbers Event Performance guarded with
IsAdminRole; the `performance` module key stays in CRM_MODULES only so the
Permissions grid can show the row as locked (frontend lib/constants.js).

NO PAGINATION. A matrix is read across the whole set and the totals bar is over
every row; the catalogue is a few hundred editions and the row is a small dict.
"""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from accounts.permissions import IsAdminRole
from events.models import Event

from . import services


class PerformanceMatrixViewSet(ViewSet):
    permission_classes = [IsAdminRole]

    def list(self, request):
        view = request.query_params.get("view") or services.VIEW_UPCOMING
        if view not in services.VIEWS:
            return Response({"detail": f"view must be one of {', '.join(services.VIEWS)}."},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(services.build_payload(view=view, user=request.user))

    @action(detail=True, methods=["patch"], url_path="verdict")
    def verdict(self, request, pk=None):
        event = get_object_or_404(Event, pk=pk)
        value = (request.data.get("verdict") or "").strip()
        if value and value not in Event.Verdict.values:
            return Response({"verdict": [f"Must be one of: {', '.join(Event.Verdict.values)}."]},
                            status=status.HTTP_400_BAD_REQUEST)
        # queryset.update, not save(): Event.save() re-derives nine columns and
        # re-resolves the sales owner, none of which a verdict should touch.
        Event.objects.filter(pk=event.pk).update(verdict=value, updated_at=timezone.now())
        return Response({"id": event.pk, "verdict": value})
