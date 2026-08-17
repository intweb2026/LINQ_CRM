"""
accounts/permissions.py
────────────────────────
DRF permission classes and RBAC queryset mixin.
"""
from rest_framework.permissions import BasePermission

# The one account permitted to destroy a whole module's data.
#
# DECLARED ONCE, here. Five endpoints answer "clear all" now (bookings, events,
# ticket central, paper review, proposal submission) and each used to carry its own
# `request.user.username != 'HP'` literal. Five copies of an identity check is five
# chances for one of them to be dropped in a refactor and quietly widen who can
# wipe a table — and the widening would not show up until someone did.
HP_USERNAME = "HP"


class IsHPAccount(BasePermission):
    """
    ONLY the HP account. Not admins, not is_all_access roles, not superusers.

    This is deliberately NOT IsAdminRole with a narrower message. IsAdminRole admits
    three kinds of caller (role == admin, is_all_access, and HP), and every one of
    those is a legitimate administrator who must nevertheless NOT be able to empty a
    module — the whole point of this class is that the destructive action has one
    owner. `is_superuser` is not consulted for the same reason: a Django superuser
    can already do anything through /admin, but the CRM's own wipe endpoints answer
    to this account and no other.

    Attached per-action with `permission_classes=[IsHPAccount]`, which REPLACES the
    viewset's module permission rather than adding to it. That is correct here and
    not a loosening: crm_permission already lets the HP account through every module
    gate, so the union with a module check would be exactly this test.
    """
    message = "This action is restricted to the HP account."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.username == HP_USERNAME
        )


class IsAdminRole(BasePermission):
    message = "Admin role required."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        # HP bypasses everything
        if request.user.username == HP_USERNAME:
            return True
        # Standard admin role check
        if request.user.is_admin:
            return True
        # A team flagged is_all_access also qualifies. This read a per-user
        # CustomRole until access moved onto the team.
        return bool(request.user.has_all_access)
    

class IsSalesOrAdmin(BasePermission):
    message = "Authentication required."

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated


class IsSalesOrAdminOrReadOnly(BasePermission):
    message = "Only sales teams and admins are allowed to edit bookings."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        from rest_framework.permissions import SAFE_METHODS
        if request.method in SAFE_METHODS:
            return True
        return request.user.role in ("admin", "sales")


class RBACMixin:
    """
    Mixin for ViewSets.
    Adds rbac_filter() and rbac_filter_invoice() helpers that
    transparently scope querysets based on user role.
    """
    permission_classes = [IsSalesOrAdminOrReadOnly]

    def rbac_filter(self, qs, event_code_field="event_code", owner_path=None):
        user = self.request.user
        if user.is_admin:
            return qs

        from django.db.models import Q

        codes = user.assigned_event_codes() or []

        # Build event_code OR clause (used as either primary or secondary filter).
        #
        # iexact per code, not icontains, and both halves of that matter. The
        # stored codes disagree with the catalogue on CASE — `Feb2027_BIZ-PM`
        # against the catalogue's `FEB2027_BIZ-PM` — so an exact match drops 9
        # delegate rows out of the list of the person who sold them, which is why
        # this is not a plain `__in`. And a SUBSTRING match over-grants, because
        # `SFU - AD` is contained in `BSFU - AD`: whoever owns the first event
        # would be handed every booking on the second. That was harmless only for
        # as long as assigned_event_codes() returned nothing for everybody.
        ec_query = Q()
        for code in codes:
            ec_query |= Q(**{f"{event_code_field}__iexact": code})

        # You can see a row on an event you hold, OR one you personally sold.
        #
        # `owner_path` is how the row reaches its sales executive. BookEvent holds
        # the FK itself; BookDelegate reaches it through its invoice, and used to
        # get no ownership clause at all because this looked only for a field on
        # the model. That left the two halves of one module disagreeing: a person
        # could open an invoice they sold and find none of the delegates on it.
        if owner_path is None and hasattr(qs.model, "sales_executive"):
            owner_path = "sales_executive"

        if owner_path:
            combined = Q(**{owner_path: user})
            if ec_query:
                combined |= ec_query
            return qs.filter(combined)

        # No ownership path and no events: scoped to nothing, never to everything.
        if not ec_query:
            return qs.none()
        return qs.filter(ec_query)

    def rbac_filter_invoice(self, qs):
        """Scope a model that hangs off an invoice rather than holding the
        sales executive itself, which today means BookDelegate."""
        return self.rbac_filter(qs, owner_path="invoice__sales_executive")
