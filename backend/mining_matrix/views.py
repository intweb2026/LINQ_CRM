"""
mining_matrix/views.py
───────────────────────
Read-only. The Mining Resource Matrix owns no table and writes nothing.

GET /api/mining-matrix/                     the matrix, default `upcoming` view
GET /api/mining-matrix/?view=all            every event, past editions included
GET /api/mining-matrix/?view=unlinked       unmined work no upcoming event covers
GET /api/mining-matrix/?include_zero=1      keep the rows with nothing outstanding

NO PAGINATION, DELIBERATELY. This is a matrix, not a list: the priority columns
only make sense read across the whole set, and the footer totals are over every
row. The catalogue is a few hundred events and the payload is one small dict per
row, so the whole thing is one response.

NOT A ModelViewSet, for the same reason performance_matrix is not — there is no
model here to serialise. The rows are built in services.py and pass straight
through DRF's renderer.
"""
import logging

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from accounts.crm_permissions import crm_permission

from . import services


logger = logging.getLogger(__name__)


def _flag(request, name, default=False):
    """A query param read as a boolean. Absent means the default."""
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


class MiningMatrixViewSet(ViewSet):
    """
    Gated on its own CRM module rather than on ticket_central.

    The matrix aggregates Ticket Central, so gating it on that module would have
    been the smaller change — but it is a planning surface for a different
    audience (whoever schedules mining capacity), and folding the two together
    would mean nobody could be given this without also being given the ticket
    queue and its create/update rights. See accounts.models.CRM_MODULES.

    The ROWS are still scoped by Ticket Central's own rule — see
    services.unmined_by_purpose — so holding this module never widens which
    tickets a person's figures are drawn from.
    """
    permission_classes = [crm_permission("mining_matrix")]

    def list(self, request):
        return Response(services.build_payload(
            request.user,
            view=request.query_params.get("view") or services.VIEW_UPCOMING,
            include_zero=_flag(request, "include_zero"),
        ))

    @action(detail=False, methods=["post"], url_path="sync-mailable")
    def sync_mailable(self, request):
        """
        Refresh the Mailable column from HubSpot, now.

        HERE RATHER THAN IN CREDIT CONTROL, which is where this job first got
        its button only because that is where the HubSpot client is exercised
        from. Nothing in Credit Control reads this figure. A job belongs on the
        page whose data it feeds, or nobody can find the button when the number
        looks wrong.

        Cron owns the cadence (07:00 IST). This is for after somebody has just
        reworked a list in HubSpot and wants the column to agree. Two requests
        per event code and only the stale ones are fetched, so a press right
        after a scheduled run costs almost nothing; `force` refetches every
        code regardless.

        Runs INLINE and answers when it finishes. A few hundred codes is a
        minute or two, which is acceptable for a button an admin presses
        deliberately and would not be on anybody's read path.
        """
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        args = ["--force"] if _flag(request, "force") else []
        try:
            call_command("sync_mailable_counts", *args, stdout=out, stderr=out)
        except Exception as exc:  # noqa: BLE001
            logger.exception("mining_matrix: manual mailable sync failed")
            return Response(
                {"ok": False, "output": f"{type(exc).__name__}: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        logger.info("mining_matrix: manual mailable sync by %s", request.user)
        return Response({"ok": True, "output": out.getvalue().strip()})

    # There is deliberately no `summary` action. The tab counts ride in the list
    # payload as `view_counts`, because every view shares one aggregate and one
    # catalogue read (services._context) — a second endpoint would repeat both to
    # return three integers the first response already knows.
