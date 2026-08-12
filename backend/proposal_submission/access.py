"""
proposal_submission/access.py
──────────────────────────────
The one definition of who sees what in this app. Both the queryset scope and the
MR-field stripping point here; nothing in this module duplicates the checks.

WHY NOT RBACMixin.rbac_filter
Two reasons, both concrete:

  1. It matches with `event_code__icontains` per assigned code. A user assigned
     "BIU" would therefore also receive every "BIUK - PM" row — a different event
     in a different country. That is the same unanchored-match bug already fixed
     in webhooks/event_resolver.py and in this app's event_code filter.
  2. Its second branch grants on `sales_executive=user`, and ProposalSubmission
     has no such column. The branch is dead here and its presence invites someone
     to add the FK just to satisfy the mixin.

WHY EXACT SET MEMBERSHIP IS RIGHT, NOT A BOUNDARY REGEX
Event.event_code is unique and carries no edition suffix, and a proposal's
event_code is always written back as the catalogue's canonical spelling (the
serializer resolves through the resolver and stores `match.event_code`). Both
sides of this comparison therefore originate from the same column, so `__in` is
exact, indexed, and cannot over-grant on a prefix. A boundary regex would be
strictly worse here: slower, unindexed, and solving a problem that does not exist
once codes are canonical.
"""

# Roles that may read and write the MR-internal notes, in addition to anyone with
# full visibility.
MR_ROLES = ("market_research",)


def has_full_visibility(user) -> bool:
    """
    True for the three bypasses this codebase already recognises: the admin role,
    the HP account (accounts/crm_permissions.py:53), and any custom role flagged
    is_all_access (crm_permissions.py:62).

    This is the ONLY place those three are spelled out in this app.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "username", None) == "HP":
        return True
    if getattr(user, "is_admin", False):
        return True
    custom_role = getattr(user, "custom_role", None)
    return bool(custom_role is not None and getattr(custom_role, "is_all_access", False))


def may_see_mr_fields(user) -> bool:
    """
    MR-internal notes: everyone with full visibility, plus Market Research.

    Deliberately expressed as full-visibility PLUS a role list rather than a
    fourth copy of the three checks — so a change to the bypass rule reaches this
    automatically.
    """
    if has_full_visibility(user):
        return True
    return getattr(user, "role", None) in MR_ROLES


def permitted_event_codes(user):
    """
    The user's assigned event codes, from ONE source: the User→Event M2M.

    Returns a list, possibly empty. The team columns on Event
    (market_research_senior, sales_team, team_leader, …) are deliberately NOT
    consulted: they are free-text CharFields with no relation to User, so
    deriving access from them would mean matching on a display name and would
    grant or deny on a typo.
    """
    assigned = getattr(user, "assigned_events", None)
    if assigned is None:
        return []
    return list(assigned.values_list("event_code", flat=True))


def scope_queryset(qs, user):
    """
    Restrict `qs` to the caller's assigned events.

    An empty assignment list yields .none(), never an unfiltered queryset — the
    failure mode worth guarding is a scope that silently degenerates into "see
    everything", which is exactly what returning `qs` here would do.
    """
    if has_full_visibility(user):
        return qs
    codes = permitted_event_codes(user)
    if not codes:
        return qs.none()
    return qs.filter(event_code__in=codes)


def may_use_event_code(user, code) -> bool:
    """
    Whether this user may attach a proposal to `code`.

    Creation has to be scoped too: an unscoped create would disappear from its
    own author's list the instant it saved, which reads as data loss.
    """
    if has_full_visibility(user):
        return True
    return code in set(permitted_event_codes(user))
