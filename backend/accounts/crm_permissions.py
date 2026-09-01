"""
accounts/crm_permissions.py
────────────────────────────
Factory that returns a DRF permission class for a given CRM module.

Usage in ViewSets:
    from accounts.crm_permissions import crm_permission
    permission_classes = [crm_permission("events")]

WHERE ACCESS COMES FROM
The team, and nothing else. A user inherits their team's grid by being in it;
accounts.UserPermission records the cells where one person deliberately differs.
Both are resolved in User.effective_permissions(), which is the single answer
this module and every other gate reads.

This replaced a per-user CustomRole. Under that design a team and a permission
set were two things kept in step by hand, and in the live data they had already
come apart — four people in Sales Team were carrying the Speaker Sales set, so
"what can the sales team do" had no single answer.
"""
from rest_framework.permissions import BasePermission

from .permissions import dapi_USERNAME

# Actions that only need can_view
_VIEW_ACTIONS = frozenset({
    "list", "retrieve", "stats", "role_stats", "years",
    "edition_growth", "historical_editions", "edition_bookings",
    "all_edition_growth", "my_permissions", "activity", "by_invoice",
    "logs", "events_stats",
})

# Actions that need can_create
_CREATE_ACTIONS = frozenset({
    "create", "bulk_import", "submit_mr", "run_backfill",
})

# Actions that need can_update
# NOTE: bulk_update is POST, so without it here the HTTP-method fallback below
# would map it to can_create instead of can_update.
_UPDATE_ACTIONS = frozenset({
    "update", "partial_update", "update_attendance",
    "submit_dmd", "return_to_mr", "move_member", "bulk_move",
    "assign_lead", "toggle_status", "reset_password", "move_team",
    "assign_events", "add_event", "remove_event", "archive",
    "sync_roles", "bulk_update",
})

# Actions that need can_delete
_DELETE_ACTIONS = frozenset({
    "destroy", "bulk_delete", "clear_all",
})


def has_module_action(user, module: str, action: str) -> bool:
    """
    Does `user` hold `action` ("view"/"create"/"update"/"delete") on `module`?

    Exists for actions that need MORE THAN ONE right, which the permission class
    cannot express — it maps one action to one bucket. A delegate transfer both
    creates a booking on the target event and rewrites the one being transferred
    away, so it is gated on create (by the class, via the POST fallback) AND on
    update (by the view, through this helper).

    can_view remains a prerequisite for the other three: a module you cannot open
    is not one you can write to, however the grid was filled in.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "username", None) == dapi_USERNAME:
        return True
    resolved = user.effective_permissions().get(module)
    if not resolved or not resolved.get("view"):
        return False
    return bool(resolved.get(action, False))


def has_all_records(user, module: str) -> bool:
    """
    Whether `user` sees EVERY row of `module`, rather than only the rows their
    assigned events cover. The "All records" cell of the permission grid.

    THE FOUR CALLERS, and nothing else: RBACMixin.rbac_filter,
    EventViewSet.get_queryset, paper_review/access.py and
    proposal_submission/access.py. Ticking the cell on any other module stores
    fine and grants nothing, because nothing asks.

    Deliberately NOT has_module_action(user, module, "all"), which would be the
    obvious reuse. That helper answers True for the HP account before consulting
    anything, and row scope for bookings and events is one place this codebase
    has always kept narrow: only role=admin bypasses rbac_filter, which is why a
    caller who was is_all_access and still scoped was able to delete rows they
    could not read (accounts/tests_write_scoping.py). Widening that here would be
    a change nobody asked for, hidden inside a feature about sharing one module.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    resolved = user.effective_permissions().get(module)
    # view stays the prerequisite it is for the other four cells. Redundant in
    # practice, because the permission class has already turned away anyone
    # without it before a queryset is built — but a helper that answers "sees
    # every row" for somebody who cannot open the module is a trap for whichever
    # gate is added next.
    if not resolved or not resolved.get("view"):
        return False
    return bool(resolved.get("all"))


def crm_permission(module: str):
    """Return a DRF permission class for the given CRM module."""

    class _CRMPermission(BasePermission):
        crm_module = module

        def has_permission(self, request, view):
            user = request.user
            if not user or not user.is_authenticated:
                return False

            # HP username bypasses all permission checks
            if user.username == dapi_USERNAME:
                return True

            resolved = user.effective_permissions().get(self.crm_module)

            # View is a prerequisite for all other permissions
            if not resolved or not resolved.get("view"):
                return False

            action = getattr(view, "action", None)

            if action in _VIEW_ACTIONS:
                return True
            if action in _CREATE_ACTIONS:
                return resolved["create"]
            if action in _UPDATE_ACTIONS:
                return resolved["update"]
            if action in _DELETE_ACTIONS:
                return resolved["delete"]

            # Map by HTTP method for unknown / custom actions
            method = request.method.upper()
            if method == "GET":
                return True
            if method == "POST":
                return resolved["create"]
            if method in ("PUT", "PATCH"):
                return resolved["update"]
            if method == "DELETE":
                return resolved["delete"]

            return True

    _CRMPermission.__name__ = f"CRMPermission_{module}"
    return _CRMPermission
