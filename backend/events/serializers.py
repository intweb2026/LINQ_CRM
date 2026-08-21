from django.contrib.auth import get_user_model
from rest_framework import serializers

from accounts.models import role_from_team_name

from .models import Event

User = get_user_model()


# ── Team-derived owner fallbacks ──────────────────────────────────────────────
# Six of the seven owner columns are blank on EVERY event in the live data — only
# sales_team (the SCA) was ever populated — so the drawer's Teams tab and the
# Events table rendered six empty rows on every event. The team that owns each of
# those roles already records its lead, so a blank column now resolves to that
# lead instead of to nothing.
#
# Resolved HERE rather than in the browser because /api/teams/ is gated on
# crm_permission("teams"), and only the all-access Admin team holds it: a
# client-side lookup would 403 for exactly the sales and research users who live
# on this screen, and the fallback would silently never appear for them. Server
# side it is also one query per response instead of one per consumer.
#
# Keyed on the ROLE a team's NAME implies (accounts.role_from_team_name) rather
# than on team id or slug. That mapping is already this codebase's answer to
# "which team is the telemarketing team", it is mirrored in
# frontend/src/lib/roleFromTeam.js, and accounts/tests_wire_probe.py holds the two
# copies in step — so a renamed team keeps working and a new one is picked up
# without a second mapping to maintain.
#
# DELIBERATELY ABSENT, and why:
#   sales_team             — the SCA is a genuine per-event answer and is
#                            populated on every event; it has nothing to inherit.
#   market_research_junior — a team records ONE lead, so a fallback here would
#                            print the same name as market_research_senior on
#                            every event, which reads as data where there is none.
#   event_management_team  — no team in the Teams module owns this role; it stays
#                            free text until one does.
OWNER_ROLE_SOURCES = {
    "team_leader":            User.Role.SALES,
    "telemarketing_team":     User.Role.TELEMARKETING,
    "market_research_senior": User.Role.MARKET_RESEARCH,
    "spex_team":              User.Role.SPEX,
}


def team_owner_defaults():
    """
    {owner_field: {"names": [<lead>, ...], "team": <team name>}} for the
    role-backed owner columns, skipping any role whose team is missing or has no
    lead at all.

    A TEAM MAY HAVE ANY NUMBER OF LEADS, and every one of them is returned. Sales
    Team has two in the live data — the Team.team_lead FK names one of them and
    the other is carried only by User.is_team_lead — so reading the FK alone
    reported one lead and quietly dropped the rest. There is no cap here and
    nothing downstream picks a winner: adding a third lead to a team makes three
    names appear.

    Both sources are unioned rather than one being preferred, because they can
    disagree. The FK is the PRIMARY lead and goes first, and it counts even when
    nobody remembered to tick is_team_lead on that row; the flagged members
    follow in id order so the list is stable between requests.
    """
    from teams.models import Team

    teams = sorted(
        Team.objects.filter(is_archived=False).select_related("team_lead"),
        key=lambda t: t.pk,
    )
    if not teams:
        return {}

    # One query for everyone flagged as a lead in any of these teams, rather than
    # a members lookup per team. Both queries here are constant in the number of
    # events being serialised, which is the property that matters.
    flagged = {}
    for user in User.objects.filter(
        is_team_lead=True, team_id__in=[t.pk for t in teams]
    ).order_by("id"):
        flagged.setdefault(user.team_id, []).append(
            user.get_full_name() or user.username
        )

    by_role = {}
    for team in teams:
        role = role_from_team_name(team.name)
        # Lowest pk wins. Two teams can imply the same role ("Sales Team" and
        # "Inside Sales" both say SALES), and without a tie-break the answer
        # would follow whatever order the database happened to return and could
        # differ between two requests for the same event. `teams` is sorted, so
        # the first role to land is the one that keeps it.
        if not role or role in by_role:
            continue

        names = []
        if team.team_lead_id:
            names.append(team.team_lead.get_full_name() or team.team_lead.username)
        for name in flagged.get(team.pk, []):
            if name not in names:
                names.append(name)
        if names:
            by_role[role] = {"names": names, "team": team.name}

    return {
        field: by_role[role]
        for field, role in OWNER_ROLE_SOURCES.items()
        if role in by_role
    }


class OwnerResolutionMixin:
    """
    Adds `owner_resolution` — the inherited owners for THIS event, keyed by the
    column they stand in for.

    Only columns that actually fell back appear, so a caller can tell an
    inherited name from one stored on the event and label it as such; a stored
    value always wins and is simply absent from this dict.

    The team lookup is memoised on the serializer instance. DRF's many=True
    reuses one child serializer for every row, so this is one query per response
    rather than one per event — the difference between 1 and 218 on a full walk
    of the events table.
    """

    # Values that LOOK stored but mean "nothing is assigned", so a column holding
    # one still inherits. NewEventModal wrote "—" into every owner column it had
    # no editor for, and imported columns hold whitespace where a human meant
    # nothing; either would otherwise read as a real answer and suppress the
    # team's, leaving the row blank in the UI for the opposite reason.
    _BLANK_OWNER_VALUES = {"", "-", "–", "—"}

    def get_owner_resolution(self, obj):
        defaults = getattr(self, "_owner_defaults", None)
        if defaults is None:
            defaults = self._owner_defaults = team_owner_defaults()
        return {
            field: value
            for field, value in defaults.items()
            if (getattr(obj, field, "") or "").strip() in self._BLANK_OWNER_VALUES
        }


class UserMiniSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ["id", "username", "email", "full_name", "role"]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class EventListSerializer(OwnerResolutionMixin, serializers.ModelSerializer):
    sales_executive_name = serializers.SerializerMethodField()
    assigned_sales_users = UserMiniSerializer(source="assigned_users", many=True, read_only=True)
    owner_resolution     = serializers.SerializerMethodField()

    class Meta:
        model  = Event
        fields = [
            "id", "event_code", "event_date", "end_date", "location", "website", "web_bookings",
            "nearest_related_event", "event_type", "website_live_date", "sales_check", "vr1_sent_status",
            "sales_team", "team_leader", "telemarketing_team", "spex_team",
            "market_research_senior", "market_research_junior", "event_management_team", "official_event_name",
            "email_marketing_name", "branding_name", "annualisation", "date_format", "related_event_1",
            "related_event_2", "related_event_3", "upcoming_event_1", "upcoming_event_2", "upcoming_event_3",
            "status", "event_status",
            # Legacy/system fields for full-stack API contract safety
            "name", "official_name", "city", "country", "venue", "accepting_web_bookings",
            "tele_marketing_team", "market_research_team", "content_check", "marketing_check",
            "sales_executive", "sales_executive_name", "assigned_sales_users",
            "owner_resolution",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_sales_executive_name(self, obj):
        if obj.sales_executive_id:
            u = obj.sales_executive
            return u.get_full_name() or u.username
        return None


class EventDetailSerializer(OwnerResolutionMixin, serializers.ModelSerializer):
    sales_executive_name = serializers.SerializerMethodField()
    assigned_sales_users = UserMiniSerializer(source="assigned_users", many=True, read_only=True)
    total_bookings       = serializers.SerializerMethodField()
    pending_bookings     = serializers.SerializerMethodField()
    owner_resolution     = serializers.SerializerMethodField()

    class Meta:
        model  = Event
        fields = [
            "id", "event_code", "event_date", "end_date", "location", "website", "web_bookings",
            "nearest_related_event", "event_type", "website_live_date", "sales_check", "vr1_sent_status",
            "sales_team", "team_leader", "telemarketing_team", "spex_team",
            "market_research_senior", "market_research_junior", "event_management_team", "official_event_name",
            "email_marketing_name", "branding_name", "annualisation", "date_format", "related_event_1",
            "related_event_2", "related_event_3", "upcoming_event_1", "upcoming_event_2", "upcoming_event_3",
            "status", "event_status",
            # Legacy/system fields for full-stack API contract safety
            "name", "official_name", "city", "country", "venue", "accepting_web_bookings",
            "tele_marketing_team", "market_research_team", "content_check", "marketing_check",
            "sales_executive", "sales_executive_name", "assigned_sales_users",
            "total_bookings", "pending_bookings", "owner_resolution",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_sales_executive_name(self, obj):
        if obj.sales_executive_id:
            u = obj.sales_executive
            return u.get_full_name() or u.username
        return None

    def get_total_bookings(self, obj):
        from book_event.models import BookEvent
        return BookEvent.objects.filter(event_code=obj.event_code).count()

    def get_pending_bookings(self, obj):
        from book_event.models import BookEvent
        return BookEvent.objects.filter(event_code=obj.event_code, payment_status="Pending").count()


class EventWriteSerializer(serializers.ModelSerializer):
    assigned_user_ids = serializers.ListField(
        child=serializers.IntegerField(), write_only=True, required=False, default=list
    )

    class Meta:
        model  = Event
        fields = [
            "event_code", "event_date", "end_date", "location", "website", "web_bookings",
            "nearest_related_event", "event_type", "website_live_date", "sales_check", "vr1_sent_status",
            "sales_team", "team_leader", "telemarketing_team", "spex_team",
            "market_research_senior", "market_research_junior", "event_management_team", "official_event_name",
            "email_marketing_name", "branding_name", "annualisation", "date_format", "related_event_1",
            "related_event_2", "related_event_3", "upcoming_event_1", "upcoming_event_2", "upcoming_event_3",
            "status",
            # Legacy/system fields
            "name", "official_name", "city", "country", "venue", "accepting_web_bookings",
            "tele_marketing_team", "market_research_team", "content_check", "marketing_check",
            "sales_executive", "assigned_user_ids",
        ]

    def validate_event_code(self, value):
        return value.upper().strip()

    def _sync_assigned_users(self, instance, user_ids):
        users = User.objects.filter(pk__in=user_ids)
        instance.assigned_users.set(users)

    def create(self, validated_data):
        user_ids = validated_data.pop("assigned_user_ids", [])
        instance = super().create(validated_data)
        self._sync_assigned_users(instance, user_ids)
        return instance

    def update(self, instance, validated_data):
        user_ids = validated_data.pop("assigned_user_ids", None)
        instance = super().update(instance, validated_data)
        if user_ids is not None:
            self._sync_assigned_users(instance, user_ids)
        return instance
