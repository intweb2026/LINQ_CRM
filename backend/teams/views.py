from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import Team, TeamActivityLog, TeamPermission
from .serializers import TeamSerializer, TeamActivityLogSerializer
from accounts.permissions import IsAdminRole
from accounts.crm_permissions import crm_permission
from teams.models import Team, TeamPermission


class TeamViewSet(viewsets.ModelViewSet):
    permission_classes = [crm_permission("teams")]
    serializer_class = TeamSerializer

    def get_queryset(self):
        # prefetch: the serializer renders every team's grid, so without this a
        # board of 7 teams costs 7 extra queries on every list.
        #
        # `members` and `team_lead` joined the list after measuring: 7 teams cost 22
        # queries, three per team, for the grid plus a COUNT for member_count, a
        # filtered query for team_leads and an FK fetch for the lead's name. The
        # serializer now reads all three off these, which is 4 queries for the whole
        # board however many teams it holds.
        qs = (
            Team.objects
            .select_related("team_lead")
            .prefetch_related("permissions", "members")
            .order_by("name")
        )
        show_archived = self.request.query_params.get("archived") == "1"
        if not show_archived:
            qs = qs.filter(is_archived=False)
        return qs

    def perform_create(self, serializer):
        team = serializer.save()
        TeamActivityLog.objects.create(
            action_type=TeamActivityLog.ActionType.TEAM_CREATED,
            team=team,
            moved_by=self.request.user,
            notes=f"Team '{team.name}' created",
        )

    def perform_update(self, serializer):
        old_name = serializer.instance.name
        team = serializer.save()
        if team.name != old_name:
            TeamActivityLog.objects.create(
                action_type=TeamActivityLog.ActionType.TEAM_RENAMED,
                team=team,
                moved_by=self.request.user,
                notes=f"Renamed from '{old_name}' to '{team.name}'",
            )

    def destroy(self, request, *args, **kwargs):
        team = self.get_object()
        member_count = team.members.count()
        if member_count > 0:
            return Response(
                {
                    "detail": (
                        f"Team '{team.name}' still has {member_count} member(s). "
                        "Move or remove all members before deleting."
                    ),
                    "member_count": member_count,
                },
                status=status.HTTP_409_CONFLICT,
            )
        TeamActivityLog.objects.create(
            action_type=TeamActivityLog.ActionType.TEAM_DELETED,
            team=team,
            moved_by=request.user,
            notes=f"Team '{team.name}' deleted",
        )
        return super().destroy(request, *args, **kwargs)

    # ── Member operations ──────────────────────────────────────────────────────

    @action(detail=False, methods=["post"], url_path="move-member")
    def move_member(self, request):
        """POST /api/teams/move-member/"""
        user_id   = request.data.get("user_id")
        dest_id   = request.data.get("destination_team_id")

        if not user_id:
            return Response({"detail": "user_id required."}, status=status.HTTP_400_BAD_REQUEST)

        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        source_team = user.team
        dest_team   = None

        if dest_id:
            try:
                dest_team = Team.objects.get(pk=dest_id)
            except Team.DoesNotExist:
                return Response({"detail": "Destination team not found."}, status=status.HTTP_404_NOT_FOUND)

        user.team = dest_team
        user.save()  # full save so User.save() role-sync persists correctly

        log_team = dest_team or source_team
        if log_team:
            TeamActivityLog.objects.create(
                action_type=TeamActivityLog.ActionType.MEMBER_MOVED,
                team=log_team,
                user=user,
                moved_by=request.user,
                source_team=source_team,
                destination_team=dest_team,
                notes=(
                    f"{user.username} moved from "
                    f"'{source_team.name if source_team else 'Unassigned'}' "
                    f"to '{dest_team.name if dest_team else 'Unassigned'}'"
                ),
            )

        return Response({
            "user_id":             user.id,
            "username":            user.username,
            "role":                user.role,
            "source_team_id":      source_team.id if source_team else None,
            "destination_team_id": dest_team.id if dest_team else None,
        })

    @action(detail=True, methods=["post"], url_path="bulk-move")
    def bulk_move(self, request, pk=None):
        """POST /api/teams/{id}/bulk-move/ — move all members to another team (or unassign)."""
        team    = self.get_object()
        dest_id = request.data.get("destination_team_id")
        dest    = None

        if dest_id:
            try:
                dest = Team.objects.get(pk=dest_id)
            except Team.DoesNotExist:
                return Response({"detail": "Destination team not found."}, status=status.HTTP_404_NOT_FOUND)

        from django.contrib.auth import get_user_model
        User = get_user_model()
        members = list(team.members.all())
        for m in members:
            m.team = dest
            m.save()

        if members and (dest or team):
            TeamActivityLog.objects.create(
                action_type=TeamActivityLog.ActionType.MEMBER_MOVED,
                team=team,
                moved_by=request.user,
                destination_team=dest,
                notes=f"{len(members)} member(s) bulk-moved to '{dest.name if dest else 'Unassigned'}'",
            )

        return Response({"moved": len(members)})

    @action(detail=True, methods=["post"], url_path="assign-lead")
    def assign_lead(self, request, pk=None):
        """POST /api/teams/{id}/assign-lead/"""
        team     = self.get_object()
        user_id  = request.data.get("user_id")
        user_ids = request.data.get("user_ids")  # list of user IDs

        from django.contrib.auth import get_user_model
        User = get_user_model()

        # Clear is_team_lead for all members of the team first
        team.members.update(is_team_lead=False)

        leads = []
        if user_ids is not None:
            # Multi-lead assignment
            clean_ids = []
            for uid in user_ids:
                try:
                    if uid:
                        clean_ids.append(int(uid))
                except (ValueError, TypeError):
                    pass
            
            if clean_ids:
                leads = list(User.objects.filter(pk__in=clean_ids, team=team))
                User.objects.filter(pk__in=[u.id for u in leads]).update(is_team_lead=True)
        elif user_id:
            # Single-lead assignment (backward compatibility)
            try:
                lead = User.objects.get(pk=user_id, team=team)
                lead.is_team_lead = True
                lead.save(update_fields=["is_team_lead"])
                leads = [lead]
            except User.DoesNotExist:
                return Response({"detail": "User not found or not in this team."}, status=status.HTTP_404_NOT_FOUND)

        # Sync the primary team_lead ForeignKey to the first lead in the list
        primary_lead = leads[0] if leads else None
        team.team_lead = primary_lead
        team.save(update_fields=["team_lead"])

        TeamActivityLog.objects.create(
            action_type=TeamActivityLog.ActionType.LEAD_ASSIGNED,
            team=team,
            user=primary_lead,
            moved_by=request.user,
            notes=f"Leads set to {', '.join([u.username for u in leads]) if leads else 'None'}",
        )

        return Response({
            "team_id":        team.id,
            "team_lead_id":   team.team_lead_id,
            "team_lead_name": team.team_lead.username if team.team_lead else None,
            "team_leads":     [{"id": u.id, "name": u.get_full_name() or u.username} for u in leads],
        })

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        """POST /api/teams/{id}/archive/ — toggle archive state."""
        team = self.get_object()
        team.is_archived = not team.is_archived
        team.save(update_fields=["is_archived"])

        TeamActivityLog.objects.create(
            action_type=TeamActivityLog.ActionType.TEAM_ARCHIVED,
            team=team,
            moved_by=request.user,
            notes=f"Team {'archived' if team.is_archived else 'unarchived'}",
        )
        return Response({"is_archived": team.is_archived})

    @action(detail=True, methods=["put"], url_path="permissions",
            permission_classes=[crm_permission("roles")])
    def set_permissions(self, request, pk=None):
        """
        PUT /api/teams/{id}/permissions/ — replace this team's grid.

        Body: {"permissions": [{"module": "events", "can_view": true, ...}, ...],
               "is_all_access": false}

        THE TEAM IS THE ROLE, so this is where a whole team's access is decided
        and everyone in it moves together. Someone who needs to differ gets a
        delta at /api/users/{id}/permissions/ rather than a team of their own.

        Gated on the `roles` module rather than `teams`: renaming a team and
        deciding what it may do are different jobs, and only the second is
        dangerous. Both endpoints that write a grid answer to the same right.
        """
        from accounts.views import _clean_permission_rows
        from accounts.serializers import team_permission_matrix

        team = self.get_object()
        items = request.data if isinstance(request.data, list) else request.data.get("permissions", [])
        cleaned, error = _clean_permission_rows(items, allow_null=False)
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            if "is_all_access" in request.data:
                team.is_all_access = bool(request.data["is_all_access"])
                team.save(update_fields=["is_all_access"])
            # Replaced wholesale rather than upserted. The payload is the entire
            # grid, so a module missing from it has been turned off, and an
            # upsert would leave its old row standing and silently keep granting.
            team.permissions.all().delete()
            TeamPermission.objects.bulk_create([
                TeamPermission(team=team, module=module, **cells)
                for module, cells in cleaned.items()
            ])

        TeamActivityLog.objects.create(
            action_type=TeamActivityLog.ActionType.PERMISSIONS_CHANGED,
            team=team,
            moved_by=request.user,
            notes=f"Permissions updated for '{team.name}'",
        )
        team.refresh_from_db()
        return Response({
            "id": team.id,
            "is_all_access": team.is_all_access,
            "permissions": team_permission_matrix(team),
        })

    @action(detail=True, methods=["get"], url_path="activity")
    def activity(self, request, pk=None):
        """GET /api/teams/{id}/activity/"""
        team = self.get_object()
        logs = team.activity_logs.select_related(
            "user", "moved_by", "source_team", "destination_team"
        )[:50]
        return Response(TeamActivityLogSerializer(logs, many=True).data)
