"""
ticket_central/scoping.py
──────────────────────────
Who may see which ticket — ONE copy of the rule.

It used to live inline in TicketViewSet.get_queryset and nowhere else, which was
fine while Ticket Central was the only thing reading tickets. The Mining Resource
Matrix reads them too, and it aggregates them into figures a user then CLICKS
THROUGH to the ticket list. If the two disagreed about scope, the matrix would
promise "412 unmined links" and the table it navigates to would show a different
number, with nothing on either screen explaining the gap. So the predicate is
declared here and both callers ask it.

THE RULE ITSELF IS UNCHANGED — see the comments below, moved verbatim from the
view.
"""
from django.db.models import Q

from .models import Ticket

# Roles that see every ticket regardless of who raised it.
#
# data_mining is here because the module is a two-phase handover: MR raises a
# ticket, DMD works it. Scoping DMD to rows they created would leave the
# queue they exist to service invisible, and submit_dmd/return_to_mr operate
# through this queryset. Admin is exempt as everywhere else in the CRM.
UNSCOPED_ROLES = ("admin", "data_mining")


def sees_every_ticket(user):
    """True when this user's role is exempt from author scoping."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return getattr(user, "role", None) in UNSCOPED_ROLES or bool(
        getattr(user, "is_admin", False)
    )


def scope_tickets(queryset, user):
    """
    Narrow `queryset` to the tickets `user` may see.

    Scoped to the person who added the row. This REVERSES the earlier
    product spec, which made Ticket Central deliberately cross-team; the
    change was requested directly. Two things follow, both real:

     · The 42,912 migrated rows all carry created_by = the HP admin and a
       legacy Zoho name in added_user_text ("zoho_linq-corporate" on
       39,207 of them). None of those match a current username, so a
       scoped role sees only what it raises from here on.
     · rbac_filter() is still not used. Scoping is by author, not by
       assigned event code, so the event-code machinery does not apply.
    """
    if sees_every_ticket(user):
        return queryset
    own = Q(created_by=user)
    if getattr(user, "username", ""):
        own |= Q(added_user_text__iexact=user.username)
    return queryset.filter(own)


def visible_tickets(user):
    """The unadorned ticket queryset this user may read. No select_related."""
    return scope_tickets(Ticket.objects.all(), user)
