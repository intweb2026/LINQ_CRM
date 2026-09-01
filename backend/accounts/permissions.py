"""
accounts/permissions.py
────────────────────────
DRF permission classes and RBAC queryset mixin.
"""
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

# The one account permitted to destroy a whole module's data.
#
# DECLARED ONCE, here. Five endpoints answer "clear all" now (bookings, events,
# ticket central, paper review, proposal submission) and each used to carry its own
# `request.user.username != 'HP'` literal. Five copies of an identity check is five
# chances for one of them to be dropped in a refactor and quietly widen who can
# wipe a table — and the widening would not show up until someone did.
dapi_USERNAME = "HP"


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
            user and user.is_authenticated and user.username == dapi_USERNAME
        )


def is_super_admin(user) -> bool:
    """
    The rule IsAdminRole enforces, as a plain function.

    Three kinds of caller qualify, and they always have: the HP account, anyone
    holding role=admin, and anyone whose team is flagged is_all_access. Lifted
    out of the permission class because the team-manager gates below ask exactly
    the same question, and a second hand-written copy of a three-clause admin
    test is a second chance for one clause to go missing.
    """
    if not (user and getattr(user, "is_authenticated", False)):
        return False
    if getattr(user, "username", None) == dapi_USERNAME:
        return True
    if user.is_admin:
        return True
    return bool(user.has_all_access)


def managed_team_id(user):
    """The team this caller manages, or None. Super admins manage no ONE team."""
    if not (user and getattr(user, "is_authenticated", False)):
        return None
    if is_super_admin(user):
        return None
    return getattr(user, "managed_team_id", None)


def assert_can_manage_user(actor, target):
    """
    Raise unless `actor` may WRITE to the account `target`.

    Three answers, in this order:

      * a super admin may write to anybody — unchanged behaviour;
      * a manager may write only to accounts sitting in the team they were
        given, and never to a super admin who happens to sit there. Without that
        second clause a manager could reset an administrator's password out of
        their own team page and sign in as them, which is the whole restriction
        undone in two clicks;
      * anybody else is left exactly as they were, governed by the users-module
        grid alone. A grid-granted account with no managed team is not narrowed
        by this feature.
    """
    if is_super_admin(actor):
        return
    team_id = managed_team_id(actor)
    if team_id is None:
        return
    if is_super_admin(target):
        raise PermissionDenied(
            "Administrator accounts are managed by a super admin."
        )
    if target.team_id != team_id:
        raise PermissionDenied(
            "You can only manage users in the team you manage."
        )


def assert_can_place_in_team(actor, team_id):
    """
    Raise unless `actor` may put an account INTO the team `team_id`.

    The mirror of assert_can_manage_user, and it is a separate question. That one
    asks whether the person being edited is already the manager's to touch; this
    asks where they are allowed to end up. Reaching a user inside your own team
    and then moving them into Sales is creating a Sales account by another route,
    so both ends of a move are checked.

    `None` means unassigned, which is likewise out of reach for a manager.
    """
    if is_super_admin(actor):
        return
    managed = managed_team_id(actor)
    if managed is None:
        return
    if team_id is None or int(team_id) != managed:
        raise PermissionDenied(
            "You can only place users in the team you manage."
        )


class IsAdminRole(BasePermission):
    message = "Admin role required."

    def has_permission(self, request, view):
        return is_super_admin(request.user)
    

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

    # Which permission-grid module this viewset's rows belong to, so rbac_filter
    # can honour that module's "all" cell. Stated explicitly rather than read off
    # permission_classes[0].crm_module: the two are the same string today, and a
    # viewset that swapped its permission class would silently change who sees
    # every row.
    #
    # None means no module owns these rows, and the scope stays as it was.
    rbac_module = None

    def rbac_filter(self, qs, event_code_field="event_code", owner_path=None):
        user = self.request.user
        if user.is_admin:
            return qs

        # Granted every row in this module by the grid. Same answer as is_admin
        # for these rows and only these rows — see accounts.models.PERM_ACTIONS.
        if self.rbac_module:
            from .crm_permissions import has_all_records
            if has_all_records(user, self.rbac_module):
                return qs

        from django.db.models import Q

        # visible_event_codes(), NOT assigned_event_codes(): the first is the
        # second widened to everyone who names this caller as their reporting
        # manager. See accounts.models.User.data_scope_user_ids for the rule.
        codes = user.visible_event_codes() or []

        # The people this caller stands in for. One id for everybody except a
        # lead, who also carries the active accounts mapped under them.
        scope_ids = user.data_scope_user_ids() or [user.pk]

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
            # `__in` over scope_ids rather than `= user`, so a lead reaches a row
            # one of their reports personally sold even on an event the lead
            # holds no assignment for. Without this the lead's two grant routes
            # would disagree: the event-code half already covers the reports.
            combined = Q(**{f"{owner_path}__in": scope_ids})
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
