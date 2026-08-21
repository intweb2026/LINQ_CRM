"""
accounts/serializers.py
"""
import logging

from django.contrib.auth import get_user_model
from rest_framework import serializers
from events.models import Event
from .models import CRM_MODULES, PERM_ACTIONS, UserPermission

User = get_user_model()

log = logging.getLogger(__name__)


def _route_new_user_events(user):
    """
    Give `user` the events that already name them as SCA, and log what happened.

    Swallows its own failures on purpose. Routing is a repair pass over other
    rows; if it breaks, the account itself must still be created, and the same
    work is available on demand as `manage.py route_event_owners`. The exception
    is logged with a traceback rather than dropped.
    """
    try:
        from events.owner_routing import route_events_for_user
        report = route_events_for_user(user)
    except Exception:
        log.exception("event routing failed for user %s", getattr(user, "pk", None))
        return None

    if report["routed"]:
        log.info("routed %d event(s) to %s on save",
                 len(report["routed"]), user.username)
    return report

# Distinguishes "the request did not mention this field" from "the request set it
# to null". Only the second is an instruction, and conflating the two made every
# nullable FK on UserWriteSerializer impossible to clear — see update().
_MISSING = object()


def team_permission_matrix(team):
    """
    {module: {view, create, update, delete}} for a team, every module present.

    Densified deliberately. A sparse map would make the UI read a missing module
    as a missing ANSWER and have to guess; the answer for a module with no row is
    "no", and it is stated.
    """
    matrix = {m: {a: False for a in PERM_ACTIONS} for m in CRM_MODULES}
    if team is None:
        return matrix
    if team.is_all_access:
        return {m: {a: True for a in PERM_ACTIONS} for m in CRM_MODULES}
    for row in team.permissions.all():
        if row.module in matrix:
            matrix[row.module] = {a: bool(getattr(row, f"can_{a}")) for a in PERM_ACTIONS}
    return matrix


class UserPermissionSerializer(serializers.ModelSerializer):
    """
    One person's delta against their team, per module.

    Every cell is nullable and null means INHERIT, so `allow_null` is not
    politeness here — without it a cell the caller left alone would be rejected
    as required, and the only expressible states would be grant and revoke.
    """
    can_view   = serializers.BooleanField(allow_null=True, required=False)
    can_create = serializers.BooleanField(allow_null=True, required=False)
    can_update = serializers.BooleanField(allow_null=True, required=False)
    can_delete = serializers.BooleanField(allow_null=True, required=False)

    class Meta:
        model  = UserPermission
        fields = ["module", "can_view", "can_create", "can_update", "can_delete"]


class EventMiniSerializer(serializers.ModelSerializer):
    status = serializers.ReadOnlyField(source="event_status")

    class Meta:
        model  = Event
        fields = ["id", "event_code", "name", "status"]


class UserListSerializer(serializers.ModelSerializer):
    assigned_events = EventMiniSerializer(many=True, read_only=True)
    full_name       = serializers.SerializerMethodField()
    # allow_null on every one of these traversals is load-bearing, not decoration.
    # DRF resolves a dotted source by getattr-ing each step; when `team` is None
    # the AttributeError is caught and — because a read-only field is never
    # required — the field is DROPPED FROM THE PAYLOAD ALTOGETHER rather than
    # emitted as null. So the row shape varied per user: everyone with a team had
    # `team_id`, everyone without simply had no such key, and the same for
    # mapped_lead_id. allow_null turns each of those into an explicit null, so
    # the shape is the same for every row.
    team_name       = serializers.ReadOnlyField(source='team.name', allow_null=True)
    team_id         = serializers.ReadOnlyField(source='team.id', allow_null=True)
    # Counted from the prefetched list, NOT `source='assigned_events.count'`.
    # A prefetch only serves `.all()`; `.count()` bypasses it and issues its own
    # COUNT, which was one extra query per user on every list — see the note on
    # UserViewSet.queryset.
    assigned_events_count = serializers.SerializerMethodField()
    mapped_lead_id  = serializers.ReadOnlyField(source='mapped_lead.id', allow_null=True)
    mapped_lead_name = serializers.SerializerMethodField()
    # The team's grid, this person's deltas, and what the two add up to. All
    # three, because the user form has to draw a checkbox that says "on, because
    # your team says so" differently from "on, because someone ticked it here" —
    # and the effective matrix alone cannot tell them apart.
    team_permissions      = serializers.SerializerMethodField()
    permission_overrides  = UserPermissionSerializer(many=True, read_only=True)
    effective_permissions = serializers.SerializerMethodField()
    has_all_access        = serializers.ReadOnlyField()

    class Meta:
        model  = User
        fields = [
            "id", "username", "email", "first_name", "last_name", "full_name",
            "role", "status", "is_active", "login_access", "assigned_events", "assigned_events_count",
            "date_joined", "last_login", "team_id", "team_name", "is_team_lead",
            "mapped_lead_id", "mapped_lead_name",
            "team_permissions", "permission_overrides", "effective_permissions",
            "has_all_access",
        ]
        read_only_fields = ["id", "date_joined", "last_login"]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_assigned_events_count(self, obj):
        return len(obj.assigned_events.all())

    def get_mapped_lead_name(self, obj):
        return obj.mapped_lead.get_full_name() or obj.mapped_lead.username if obj.mapped_lead else None

    def get_team_permissions(self, obj):
        """
        The team's grid, built once per TEAM rather than once per user.

        With many=True, DRF wraps a single child serializer, so this cache spans the
        whole list and forty-five users across seven teams build seven matrices
        instead of forty-five. Keyed on team_id, which is what the matrix depends
        on; None is a real key here, holding the all-denied grid for someone with
        no team.
        """
        if not hasattr(self, "_team_matrix_cache"):
            self._team_matrix_cache = {}
        key = obj.team_id
        if key not in self._team_matrix_cache:
            self._team_matrix_cache[key] = team_permission_matrix(obj.team if obj.team_id else None)
        return self._team_matrix_cache[key]

    def get_effective_permissions(self, obj):
        return obj.effective_permissions()


class UserWriteSerializer(serializers.ModelSerializer):
    # allow_blank: the edit form always sends its "new password" box, and an
    # untouched box means "leave the password alone" — create()/update() below
    # both skip a falsy value rather than writing an empty one.
    password            = serializers.CharField(write_only=True, min_length=8, required=False, allow_blank=True)
    assigned_event_ids  = serializers.ListField(
        child=serializers.IntegerField(), required=False, write_only=True
    )
    team_id = serializers.IntegerField(required=False, write_only=True, allow_null=True)
    mapped_lead_id = serializers.IntegerField(required=False, write_only=True, allow_null=True)
    # Sign-in is by email (accounts/views.py RequestOTPView), so an account
    # without one cannot be used at all. AbstractUser leaves email optional and
    # non-unique, which let two accounts share an address — and RequestOTPView's
    # `User.objects.get(email__iexact=...)` then raises MultipleObjectsReturned
    # and answers 500 for BOTH of them. Required and unique is enforced here.
    email = serializers.EmailField(required=True)

    class Meta:
        model  = User
        fields = [
            "username", "email", "first_name", "last_name",
            "password", "role", "status", "login_access", "assigned_event_ids", "team_id", "is_team_lead",
            "mapped_lead_id",
        ]

    def validate_email(self, value):
        value = (value or "").strip()
        qs = User.objects.filter(email__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Another account already uses this email address.")
        return value

    def to_representation(self, instance):
        """
        Answer writes with the READ shape.

        POST/PATCH used to echo this serializer's own fields back, which carry no
        `id`, no `full_name` and no `team_id` — so the frontend's toFrontend()
        mapped a freshly created user to `{id: undefined, name: undefined}` and the
        row it rendered from the response was blank.
        """
        return UserListSerializer(instance, context=self.context).data

    def create(self, validated_data):
        event_ids = validated_data.pop("assigned_event_ids", [])
        team_id = validated_data.pop("team_id", None)
        mapped_lead_id = validated_data.pop("mapped_lead_id", None)
        password  = validated_data.pop("password", None)
        user = User(**validated_data)
        # A role the caller NAMED beats the one the team's name implies. Without
        # this the Role field on the form was decorative: pick "Operations" for
        # someone going into "Sales Team" and User.save() overwrote it with Sales
        # before the response was even built.
        user.role_is_explicit = "role" in validated_data
        if team_id:
            from teams.models import Team
            user.team = Team.objects.filter(id=team_id).first()
        if mapped_lead_id:
            user.mapped_lead = User.objects.filter(id=mapped_lead_id).first()
        if password:
            user.set_password(password)
        user.save()
        if event_ids:
            user.assigned_events.set(Event.objects.filter(id__in=event_ids))

        # Pick up the events that already NAME this person in their SCA column.
        #
        # Event.sales_executive, the FK that events/views.py get_queryset scopes
        # a non admin by, is only ever resolved while an event row is saved. Every
        # event in the catalogue was last saved before this account existed, so
        # without this call the new user's own events keep a null FK and they see
        # an empty Events page, an empty Bookings page and an empty event
        # dropdown, with their name plainly visible in the SCA column of the rows
        # they own and nothing reporting a fault. Creating the account has to be
        # enough on its own.
        #
        # Scoped to this user and to rows with NO owner, so adding somebody can
        # never take an event off anybody. See events/owner_routing.py.
        _route_new_user_events(user)
        if user.team:
            from teams.models import TeamActivityLog
            request = self.context.get('request')
            actor = request.user if (request and request.user.is_authenticated) else None
            TeamActivityLog.objects.create(
                action_type=TeamActivityLog.ActionType.MEMBER_ADDED,
                team=user.team,
                user=user,
                moved_by=actor,
            )
        return user

    def update(self, instance, validated_data):
        # _MISSING, not None. Both of these fields declare allow_null=True, which
        # says an explicit null is meaningful and means "unassign" — but popping
        # with a None default made "the request did not mention this field" and
        # "the request set it to null" the same value, and the `is not None`
        # guards below then skipped BOTH. The result was that neither a team nor
        # a reporting manager could ever be cleared through the API: the form sent
        # null, the server answered 200, and the old value was still there.
        event_ids = validated_data.pop("assigned_event_ids", None)
        team_id = validated_data.pop("team_id", _MISSING)
        mapped_lead_id = validated_data.pop("mapped_lead_id", _MISSING)
        password  = validated_data.pop("password", None)

        old_team = instance.team
        # What this user's name resolves FROM, before the write. A change to any
        # of these changes which SCA strings point at them, so the events that
        # could not be routed under the old spelling get another chance below.
        old_identity = (instance.first_name, instance.last_name,
                        instance.username, instance.email, instance.is_active)

        # See create(): naming a role on the request makes it stick, so the form
        # can move someone into a team and still override the role that implies.
        if "role" in validated_data:
            instance.role_is_explicit = True

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if team_id is not _MISSING:
            from teams.models import Team
            instance.team = Team.objects.filter(id=team_id).first() if team_id else None

        if mapped_lead_id is not _MISSING:
            # Nobody reports to themselves. The form excludes the person from its
            # own list, but the API is reachable without it.
            instance.mapped_lead = (
                User.objects.filter(id=mapped_lead_id).exclude(pk=instance.pk).first()
                if mapped_lead_id else None
            )

        if password:
            instance.set_password(password)

        instance.save()

        if event_ids is not None:
            instance.assigned_events.set(Event.objects.filter(id__in=event_ids))

        # A rename, a new address, or a reactivation changes what the SCA text on
        # an event resolves to. Correcting the spelling of somebody's name is one
        # of the two ways a human fixes an unrouted event, so it has to actually
        # route it; the other is correcting the event.
        if (instance.first_name, instance.last_name, instance.username,
                instance.email, instance.is_active) != old_identity:
            _route_new_user_events(instance)

        new_team = instance.team
        if team_id is not None and old_team != new_team:
            from teams.models import TeamActivityLog
            request = self.context.get('request')
            actor = request.user if (request and request.user.is_authenticated) else None
            if new_team and old_team is None:
                TeamActivityLog.objects.create(
                    action_type=TeamActivityLog.ActionType.MEMBER_ADDED,
                    team=new_team, user=instance, moved_by=actor,
                )
            elif new_team and old_team:
                TeamActivityLog.objects.create(
                    action_type=TeamActivityLog.ActionType.MEMBER_MOVED,
                    team=new_team, user=instance, moved_by=actor,
                    source_team=old_team, destination_team=new_team,
                )
            elif old_team and new_team is None:
                TeamActivityLog.objects.create(
                    action_type=TeamActivityLog.ActionType.MEMBER_REMOVED,
                    team=old_team, user=instance, moved_by=actor,
                    source_team=old_team,
                )

        return instance


class AssignEventsSerializer(serializers.Serializer):
    event_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)

    def validate_event_ids(self, value):
        if Event.objects.filter(id__in=value).count() != len(value):
            raise serializers.ValidationError("One or more event IDs not found.")
        return value
