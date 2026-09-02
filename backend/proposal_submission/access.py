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
    True for four bypasses: the admin role, the HP account, a TEAM flagged
    is_all_access, and the per-module "all" cell of the permission grid.

    This is the ONLY place they are spelled out in this app. The third used to be
    a per-user CustomRole; it now lives on the team, and User.has_all_access
    covers both it and the HP account. The fourth is per module, which is the
    point of it: the other three cannot widen one module without widening all of
    them.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False):
        return True
    if getattr(user, "has_all_access", False):
        return True
    # The fourth bypass, and the only one that is per module: the "all" cell of
    # the permission grid. It is what lets one person be handed every proposal submission
    # without also being handed every booking and every event, which an
    # is_all_access team or the admin role would do. The cell is inert on any
    # module whose queryset was never row-scoped; see SCOPED_MODULES.
    #
    # Imported inside the function: crm_permissions pulls in rest_framework and
    # this module is imported from serializers as well as views.
    from accounts.crm_permissions import has_all_records
    return has_all_records(user, "proposal_submission")


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
    The codes of the events this person is the named reviewer on.

    THE SAME ANSWER paper_review/access.py gives, from the same shared function,
    and it has to be; paper_review mints a proposal through this same user. See
    accounts.user_resolution.event_codes_naming.
    """
    from accounts.user_resolution import event_codes_naming
    return event_codes_naming(user)


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
