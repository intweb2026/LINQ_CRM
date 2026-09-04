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
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from accounts.crm_permissions import crm_permission

from . import services


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

    # There is deliberately no `summary` action. The tab counts ride in the list
    # payload as `view_counts`, because every view shares one aggregate and one
    # catalogue read (services._context) — a second endpoint would repeat both to
    # return three integers the first response already knows.
