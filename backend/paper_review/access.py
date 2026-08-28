"""
paper_review/access.py
───────────────────────
The one definition of who sees what in this app. Mirrors
proposal_submission/access.py; both the queryset scope and the MR-field stripping
point here, and nothing else in this app repeats the checks.

WHY NOT RBACMixin.rbac_filter
Two concrete reasons, unchanged from proposal_submission:

  1. It scopes with `event_code__icontains` per assigned code, so a user assigned
     "BIU" would also receive every "BIUK - PM" row — a different event in a
     different country.
  2. Its second branch grants on `sales_executive=user`, a column PaperReview
     does not have. The branch is dead here.

WHY EXACT SET MEMBERSHIP
Event.event_code is unique and suffix-free, and a review's event_code is always
written back as the catalogue's canonical spelling (the serializer resolves
through paper_review/event_codes.py). Both sides of the comparison therefore come
from the same column, so `__in` is exact, indexed, and cannot over-grant on a
prefix.
"""

# Roles that may read and write internal_footnotes, in addition to anyone with
# full visibility.
MR_ROLES = ("market_research",)


def has_full_visibility(user) -> bool:
    """
    Four bypasses: the admin role, the HP account, a TEAM flagged is_all_access,
    and the per-module "all" cell of the permission grid.

    The third used to be a per-user CustomRole. It now lives on the team, and
    User.has_all_access covers both it and the HP account. The fourth is the
    narrow one, and the reason it exists: the first three are all-or-nothing
    across every module, so before it the only way to show somebody every paper
    review was to show them everything else too.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False):
        return True
    if getattr(user, "has_all_access", False):
        return True
    # The fourth bypass, and the only one that is per module: the "all" cell of
    # the permission grid. It is what lets one person be handed every paper review
    # without also being handed every booking and every event, which an
    # is_all_access team or the admin role would do. The cell is inert on any
    # module whose queryset was never row-scoped; see SCOPED_MODULES.
    #
    # Imported inside the function: crm_permissions pulls in rest_framework and
    # this module is imported from serializers as well as views.
    from accounts.crm_permissions import has_all_records
    return has_all_records(user, "paper_review")


def may_see_mr_fields(user) -> bool:
    """
    internal_footnotes: everyone with full visibility, plus Market Research.

    Expressed as full-visibility PLUS a role list rather than a fourth copy of
    the three checks, so a change to the bypass rule reaches this automatically.
    """
    if has_full_visibility(user):
        return True
    return getattr(user, "role", None) in MR_ROLES


def permitted_event_codes(user):
    """
    The user's assigned event codes, from ONE source: the User→Event M2M.

    Event's team columns (market_research_senior, sales_team, team_leader, …) are
    deliberately not consulted — they are free-text CharFields with no relation
    to User, so deriving access from them would mean matching a display name and
    would grant or deny on a typo.
    """
    assigned = getattr(user, "assigned_events", None)
    if assigned is None:
        return []
    return list(assigned.values_list("event_code", flat=True))


def scope_queryset(qs, user):
    """
    Restrict `qs` to the caller's assigned events.

    An empty assignment list yields .none(), never an unfiltered queryset — a
    scope that silently degenerates into "see everything" is the failure worth
    guarding, and returning `qs` here would be exactly that.
    """
    if has_full_visibility(user):
        return qs
    codes = permitted_event_codes(user)
    if not codes:
        return qs.none()
    return qs.filter(event_code__in=codes)


def may_use_event_code(user, code) -> bool:
    """
    Whether this user may attach a review to `code`. Creation and PATCH are
    scoped too: an unscoped write would vanish from its own author's list the
    instant it saved, which reads as data loss.
    """
    if has_full_visibility(user):
        return True
    return code in set(permitted_event_codes(user))
