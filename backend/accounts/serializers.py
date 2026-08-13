"""
accounts/serializers.py
"""
from django.contrib.auth import get_user_model
from rest_framework import serializers
from events.models import Event
from .models import CustomRole, RolePermission, CRM_MODULES

User = get_user_model()


class RolePermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model  = RolePermission
        fields = ["module", "can_view", "can_create", "can_update", "can_delete"]


class CustomRoleSerializer(serializers.ModelSerializer):
    user_count  = serializers.SerializerMethodField()
    permissions = RolePermissionSerializer(many=True, read_only=True)

    class Meta:
        model  = CustomRole
        fields = [
            "id", "name", "display_label", "color", "description",
            "is_all_access", "is_system_role", "user_count", "permissions",
            "created_at",
        ]
        read_only_fields = ["created_at", "is_system_role"]

    def get_user_count(self, obj):
        return obj.users.count()


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
    # custom_role_id, custom_role_label and mapped_lead_id. allow_null turns each
    # of those into an explicit null, so the shape is the same for every row.
    team_name       = serializers.ReadOnlyField(source='team.name', allow_null=True)
    team_id         = serializers.ReadOnlyField(source='team.id', allow_null=True)
    assigned_events_count = serializers.IntegerField(source='assigned_events.count', read_only=True)
    mapped_lead_id  = serializers.ReadOnlyField(source='mapped_lead.id', allow_null=True)
    mapped_lead_name = serializers.SerializerMethodField()
    custom_role_id    = serializers.ReadOnlyField(source='custom_role.id', allow_null=True)
    custom_role_label = serializers.ReadOnlyField(source='custom_role.display_label', allow_null=True)

    class Meta:
        model  = User
        fields = [
            "id", "username", "email", "first_name", "last_name", "full_name",
            "role", "status", "is_active", "assigned_events", "assigned_events_count",
            "date_joined", "last_login", "team_id", "team_name", "is_team_lead",
            "mapped_lead_id", "mapped_lead_name", "custom_role_id", "custom_role_label"
        ]
        read_only_fields = ["id", "date_joined", "last_login"]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_mapped_lead_name(self, obj):
        return obj.mapped_lead.get_full_name() or obj.mapped_lead.username if obj.mapped_lead else None


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
    custom_role_id = serializers.IntegerField(required=False, write_only=True, allow_null=True)
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
            "password", "role", "status", "assigned_event_ids", "team_id", "is_team_lead",
            "mapped_lead_id", "custom_role_id"
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
        custom_role_id = validated_data.pop("custom_role_id", None)
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
        if custom_role_id is not None:
            user.custom_role = CustomRole.objects.filter(id=custom_role_id).first() if custom_role_id else None
        user.save()
        if event_ids:
            user.assigned_events.set(Event.objects.filter(id__in=event_ids))
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
        event_ids = validated_data.pop("assigned_event_ids", None)
        team_id = validated_data.pop("team_id", None)
        custom_role_id = validated_data.pop("custom_role_id", None)
        mapped_lead_id = validated_data.pop("mapped_lead_id", None)
        password  = validated_data.pop("password", None)

        old_team = instance.team

        # See create(): naming a role on the request makes it stick, so the form
        # can move someone into a team and still override the role that implies.
        if "role" in validated_data:
            instance.role_is_explicit = True

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if team_id is not None:
            from teams.models import Team
            instance.team = Team.objects.filter(id=team_id).first() if team_id else None

        if mapped_lead_id is not None:
            instance.mapped_lead = User.objects.filter(id=mapped_lead_id).first() if mapped_lead_id else None

        if custom_role_id is not None:
            instance.custom_role = CustomRole.objects.filter(id=custom_role_id).first() if custom_role_id else None

        if password:
            instance.set_password(password)

        instance.save()

        if event_ids is not None:
            instance.assigned_events.set(Event.objects.filter(id__in=event_ids))

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
