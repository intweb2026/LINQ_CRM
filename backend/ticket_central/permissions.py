"""
ticket_central/permissions.py
──────────────────────────────
Role-based access for ticket operations.
"""
from rest_framework.permissions import BasePermission


class IsMarketResearchOrAdmin(BasePermission):
    message = "Market Research or Admin role required."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return request.user.role in ("market_research", "admin")


class IsDataMiningOrAdmin(BasePermission):
    message = "Data Mining or Admin role required."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return request.user.role in ("data_mining", "admin")


class IsTicketTeamOrAdmin(BasePermission):
    """Either MR or DMD can view tickets; admin sees all."""
    message = "You do not have access to Ticket Central."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return request.user.role in ("market_research", "data_mining", "admin")


def may_edit_mr_fields(user):
    """
    May this person write the Market Research half of a ticket?

    MR owns Section A and DMD owns Section B — that split is the whole point of
    the two update serializers, and a plain DMD miner must not quietly rewrite
    the brief they were handed. But a DMD LEAD or MANAGER is the person the
    miners escalate a wrong estimate or a bad link to, and until now their only
    route was to bounce the ticket back to MR and wait, or to ask an admin.

    Deliberately BOTH flags. `is_team_lead` is the tick on the user form;
    `is_team_manager` is the super-admin-assigned managed_team. They mean
    different things elsewhere in the CRM (see User.is_team_manager) and either
    one is seniority enough for this.

    Role is still checked: a Sales team lead is a lead of nothing here.
    """
    if not (user and getattr(user, "is_authenticated", False)):
        return False
    if getattr(user, "is_admin", False):
        return True
    return user.role == "data_mining" and bool(
        user.is_team_lead or user.is_team_manager
    )
