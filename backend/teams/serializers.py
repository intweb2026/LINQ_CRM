from rest_framework import serializers
from .models import Team, TeamActivityLog


class TeamSerializer(serializers.ModelSerializer):
    # Counted from the prefetched members, not `source="members.count"`. A
    # prefetch serves `.all()` only; `.count()` went back to the database for
    # every team on every list. Same reason get_team_leads below filters in Python
    # instead of asking the database per team — one prefetch answers both.
    member_count   = serializers.SerializerMethodField()
    team_lead_id   = serializers.SerializerMethodField()
    team_lead_name = serializers.SerializerMethodField()
    team_leads     = serializers.SerializerMethodField()
    # The team's grid, dense and always present. This is the role now, so every
    # caller that renders a team can render what it opens without a second
    # request per card.
    permissions    = serializers.SerializerMethodField()

    class Meta:
        model  = Team
        fields = [
            "id", "name", "slug", "color", "description",
            "member_count", "team_lead_id", "team_lead_name", "team_leads",
            "permissions", "is_all_access",
            "is_archived", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "slug", "created_at", "updated_at"]

    def get_permissions(self, obj):
        # Imported here: accounts.serializers imports nothing from teams, but
        # teams.views already depends on accounts, and a module-scope import in
        # both directions is how that becomes a cycle.
        from accounts.serializers import team_permission_matrix
        return team_permission_matrix(obj)

    def get_member_count(self, obj):
        return len(obj.members.all())

    def get_team_lead_id(self, obj):
        return obj.team_lead_id

    def get_team_lead_name(self, obj):
        if not obj.team_lead:
            return None
        return obj.team_lead.get_full_name() or obj.team_lead.username

    def get_team_leads(self, obj):
        # Filtered in Python over the prefetched members rather than
        # `obj.members.filter(...)`, which is a fresh query per team — and a second
        # one on top of the COUNT that member_count used to run. The prefetch is
        # needed for the count anyway, so the leads come out of it for free.
        return [
            {"id": u.id, "name": u.get_full_name() or u.username}
            for u in obj.members.all() if u.is_team_lead
        ]


class TeamActivityLogSerializer(serializers.ModelSerializer):
    user_name   = serializers.SerializerMethodField()
    actor_name  = serializers.SerializerMethodField()
    source_name = serializers.CharField(source="source_team.name", read_only=True, allow_null=True)
    dest_name   = serializers.CharField(source="destination_team.name", read_only=True, allow_null=True)

    class Meta:
        model  = TeamActivityLog
        fields = [
            "id", "action_type", "user_name", "actor_name",
            "source_name", "dest_name", "notes", "created_at",
        ]

    def get_user_name(self, obj):
        if not obj.user:
            return None
        return obj.user.get_full_name() or obj.user.username

    def get_actor_name(self, obj):
        if not obj.moved_by:
            return None
        return obj.moved_by.get_full_name() or obj.moved_by.username
