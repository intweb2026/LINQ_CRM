"""
accounts/views.py
──────────────────
User management — admin only.
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .permissions import IsAdminRole
from .serializers import (
    UserListSerializer, UserWriteSerializer, AssignEventsSerializer,
    UserPermissionSerializer, team_permission_matrix,
)
from .models import CRM_MODULES, PERM_ACTIONS, PERM_FIELDS, UserPermission, role_from_team_name
from .crm_permissions import crm_permission

from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.core.mail import send_mail
from django.conf import settings as django_settings
from django.utils import timezone
from datetime import timedelta
from .models import OTPToken

User = get_user_model()


def _clean_permission_rows(items, allow_null=False):
    """
    Validate a permission payload and return ({module: {can_*: value}}, error).

    Shared by the team grid and the per-user deltas so one payload shape is
    parsed in one place. `allow_null` is the difference between them: a team cell
    is a plain yes/no, while a user cell has a third state, null for inherit,
    which must survive rather than being coerced to False. bool(None) is False,
    so a single shared bool() cast here would silently turn every inherit into a
    revoke — the whole point of the delta, lost at the boundary.

    An unknown or duplicated module fails the WHOLE request. A partially applied
    permission change is worse than a rejected one.
    """
    if not isinstance(items, list):
        return None, "permissions must be a list."

    valid = set(CRM_MODULES)
    cleaned = {}
    for item in items:
        if not isinstance(item, dict):
            return None, "Each permission entry must be an object."
        module = item.get("module")
        if module not in valid:
            return None, f"Unknown module: {module}"
        if module in cleaned:
            return None, f"Duplicate module: {module}"
        cells = {}
        for field in PERM_FIELDS:
            raw = item.get(field, None if allow_null else False)
            if raw is None and allow_null:
                cells[field] = None
            elif isinstance(raw, bool):
                cells[field] = raw
            elif raw is None:
                cells[field] = False
            else:
                return None, f"{module}.{field} must be true, false or null."
        cleaned[module] = cells
    return cleaned, None


class RequestOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        if not email:
            return Response({"detail": "Email is required."}, status=400)

        success_msg = {"detail": "If an account exists with this email, a login code has been sent."}

        try:
            user = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist:
            return Response(success_msg)

        # HP bypasses OTP rate limiting entirely
        if user.username != "HP":
            recent_count = OTPToken.objects.filter(
                user=user,
                created_at__gte=timezone.now() - timedelta(hours=1),
            ).count()
            if recent_count >= 20:
                return Response({"detail": "Too many requests. Please try again later."}, status=429)

        otp_obj = OTPToken.create_for_user(user)

        try:
            send_mail(
                subject="Your IQ-HUB CRM Login Code",
                message=(
                    f"Hi {user.first_name or user.username},\n\n"
                    f"Your one-time login code is: {otp_obj.otp}\n\n"
                    f"This code expires in 5 minutes. If you didn't request this, ignore this email.\n\n"
                    f"— IQ-HUB CRM"
                ),
                from_email=django_settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Failed to send OTP email to %s", email)

        return Response(success_msg)


class VerifyOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        otp = request.data.get("otp", "").strip()

        if not email or not otp:
            return Response({"detail": "Email and OTP are required."}, status=400)

        try:
            user = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist:
            return Response({"detail": "Invalid email or code."}, status=401)

        # Dev bypass: any user can log in with "000000"
        if otp == "000000":
            token, _ = Token.objects.get_or_create(user=user)
            user.last_login = timezone.now()
            user.save(update_fields=["last_login"])
            return Response({
                "token":    token.key,
                "user_id":  user.pk,
                "email":    user.email,
                "username": user.username,
                "role":     user.role,
            })

        otp_obj = (
            OTPToken.objects.filter(user=user, otp=otp, is_used=False)
            .order_by("-created_at")
            .first()
        )

        if not otp_obj:
            return Response({"detail": "Invalid email or code."}, status=401)

        if otp_obj.attempts >= 5:
            otp_obj.is_used = True
            otp_obj.save(update_fields=["is_used"])
            return Response({"detail": "Too many attempts. Please request a new code."}, status=429)

        otp_obj.attempts += 1
        otp_obj.save(update_fields=["attempts"])

        if otp_obj.is_expired():
            otp_obj.is_used = True
            otp_obj.save(update_fields=["is_used"])
            return Response({"detail": "Code has expired. Please request a new one."}, status=401)

        otp_obj.is_used = True
        otp_obj.save(update_fields=["is_used"])

        token, _ = Token.objects.get_or_create(user=user)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])

        return Response({
            "token": token.key,
            "user_id": user.pk,
            "email": user.email,
            "username": user.username,
            "role": user.role,
        })


class CustomAuthToken(ObtainAuthToken):
    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data,
                                           context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        token, created = Token.objects.get_or_create(user=user)
        return Response({
            'token': token.key,
            'user_id': user.pk,
            'email': user.email,
            'role': user.role
        })


class UserViewSet(viewsets.ModelViewSet):
    """CRUD + event assignment. Write actions require users-module permission."""
    permission_classes = [crm_permission("users")]
    queryset = User.objects.prefetch_related("assigned_events").order_by("-date_joined")
    filterset_fields = ["role", "status", "team"]
    search_fields = ["username", "first_name", "last_name", "email"]

    def get_permissions(self):
        from rest_framework.permissions import IsAuthenticated
        # These actions are accessible to any authenticated user:
        # - my_permissions: every user must be able to fetch their own permission matrix
        # - list / retrieve: needed for user dropdowns throughout the app (e.g. MR assignment)
        # - role_stats / sync_roles / role_stats: informational
        if self.action in (
            "list", "retrieve",
            "my_permissions", "role_stats", "sync_roles",
        ):
            return [IsAuthenticated()]
        # Deciding what someone MAY DO is gated on `roles`, the same right that
        # governs a team's grid — not on `users`, which is about their name and
        # their team. Set here rather than on the @action, because this override
        # replaces permission_classes wholesale and would ignore it there.
        if self.action == "set_permissions":
            return [crm_permission("roles")()]
        return [crm_permission("users")()]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return UserWriteSerializer
        return UserListSerializer

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if user == request.user:
            return Response({"detail": "You cannot delete your own admin account."}, status=400)
        
        # Check if last admin
        if user.role == User.Role.ADMIN and User.objects.filter(role=User.Role.ADMIN).count() <= 1:
            return Response({"detail": "Cannot delete the last administrator."}, status=400)
            
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="assign_events")
    def assign_events(self, request, pk=None):
        """Replace all event assignments for this user."""
        user = self.get_object()
        ser = AssignEventsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        from events.models import Event
        events = Event.objects.filter(id__in=ser.validated_data["event_ids"])
        user.assigned_events.set(events)
        return Response({
            "user": user.username,
            "assigned_events": list(events.values("id", "event_code", "name")),
        })

    @action(detail=True, methods=["post"], url_path="add_event")
    def add_event(self, request, pk=None):
        """Add a single event to this user's assignments."""
        user = self.get_object()
        from events.models import Event
        try:
            event = Event.objects.get(id=request.data.get("event_id"))
        except Event.DoesNotExist:
            return Response({"detail": "Event not found."}, status=404)
        user.assigned_events.add(event)
        return Response({"user": user.username, "added": event.event_code})

    @action(detail=True, methods=["post"], url_path="remove_event")
    def remove_event(self, request, pk=None):
        """Remove a single event from this user's assignments."""
        user = self.get_object()
        from events.models import Event
        try:
            event = Event.objects.get(id=request.data.get("event_id"))
        except Event.DoesNotExist:
            return Response({"detail": "Event not found."}, status=404)
        user.assigned_events.remove(event)
        return Response({"user": user.username, "removed": event.event_code})

    @action(detail=True, methods=["get"])
    def logs(self, request, pk=None):
        """GET /api/users/{id}/logs/ — fetch action logs for the user."""
        user = self.get_object()
        from .models import ActionLog
        logs = ActionLog.objects.filter(user=user)[:50]
        return Response([
            {
                "id": log.id,
                "action": log.action,
                "details": log.details,
                "created_at": log.created_at
            } for log in logs
        ])

    @action(detail=True, methods=["get"])
    def events_stats(self, request, pk=None):
        """GET /api/users/{id}/events_stats/ — fetch events assigned and their expected/current revenue."""
        user = self.get_object()
        codes = user.assigned_event_codes()
        if not codes:
            return Response([])

        from events.models import Event
        events = Event.objects.filter(event_code__in=codes)

        stats = []
        for e in events:
            rev = 0
            stats.append({
                "event_code": e.event_code,
                "name": e.name,
                "expected_revenue": float(e.expected_revenue),
                "current_revenue": float(rev),
                "event_status": e.event_status
            })

        return Response(stats)

    @action(detail=True, methods=["patch"], url_path="move-team")
    def move_team(self, request, pk=None):
        """PATCH /api/users/{id}/move-team/ — Move user to a new team."""
        user = self.get_object()
        team_id = request.data.get("team_id")
        if team_id is None:
             user.team = None
             user.save()
             return Response({"user": user.username, "team": None})
        
        from teams.models import Team
        try:
            team = Team.objects.get(id=team_id)
        except Team.DoesNotExist:
            return Response({"detail": "Team not found."}, status=404)
        
        user.team = team
        user.save()
        return Response({
            "user": user.username,
            "team": team.name,
            "team_id": team.id
        })

    @action(detail=True, methods=["patch"], url_path="toggle-status")
    def toggle_status(self, request, pk=None):
        """
        PATCH /api/users/{id}/toggle-status/ — flip active/inactive.

        An ABSENT `status` means "flip it", which is what the Users drawer's
        Deactivate/Activate button sends. Requiring the field made that button
        answer 400 "Invalid status. Choose from [...]" on every single click —
        the endpoint was named toggle-status but refused to toggle anything.
        An explicit `status` is still honoured, so a caller can set `suspended`.
        """
        user = self.get_object()
        if user == request.user:
            return Response({"detail": "You cannot deactivate your own account."}, status=400)

        new_status = request.data.get("status")
        if new_status is None:
            new_status = (
                User.Status.INACTIVE if user.status == User.Status.ACTIVE
                else User.Status.ACTIVE
            )
        elif new_status not in User.Status.values:
            return Response({"detail": f"Invalid status. Choose from {User.Status.values}"}, status=400)

        user.status = new_status
        user.save()
        return Response(UserListSerializer(user, context={"request": request}).data)

    @action(detail=True, methods=["patch"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        """PATCH /api/users/{id}/reset-password/ — Reset user password."""
        user = self.get_object()
        password = request.data.get("password")
        confirm = request.data.get("confirm_password")

        if not password:
            return Response({"detail": "Password is required."}, status=400)
        if len(password) < 8:
            return Response({"detail": "Password must be at least 8 characters."}, status=400)
        if password != confirm:
            return Response({"detail": "Passwords do not match."}, status=400)

        user.set_password(password)
        user.save()
        return Response({"detail": "Password reset successfully."})

    @action(detail=False, methods=["post"], url_path="sync-roles")
    def sync_roles(self, request):
        """
        Re-derive every user's role from their current team name and update it.

        The keyword chain lives in accounts/models.py and is shared with
        User.save(); this endpoint used to carry a second hand-written copy of
        it, which is two places for the same rule to drift apart.

        Still the right escape hatch after a team is RENAMED: save() only derives
        when a user's team CHANGES, so a rename leaves existing members holding
        the role the old name implied until this is run.
        """
        updated = 0
        qs = User.objects.filter(team__isnull=False).select_related("team")
        for user in qs:
            new_role = role_from_team_name(user.team.name)
            if new_role and new_role != user.role:
                User.objects.filter(pk=user.pk).update(role=new_role)
                updated += 1
        return Response({
            "updated": updated,
            "total_with_team": qs.count(),
            "detail": f"Synced {updated} user role(s) from team names.",
        })

    @action(detail=False, methods=["get"], url_path="role-stats")
    def role_stats(self, request):
        """GET /api/users/role-stats/ — count of users per role."""
        from django.db.models import Count
        rows = (
            User.objects
            .values("role")
            .annotate(count=Count("id"))
            .order_by("role")
        )
        return Response({r["role"]: r["count"] for r in rows})

    @action(detail=False, methods=["get"], url_path="my-permissions",
            permission_classes=[IsAuthenticated])
    def my_permissions(self, request):
        """
        GET /api/users/my-permissions/ — the caller's own effective matrix.

        Team grid plus their own deltas, resolved once in
        User.effective_permissions(). The response shape is unchanged, so the
        frontend's SessionContext keeps reading it as it did.
        """
        user = request.user
        return Response({
            "is_all_access": user.has_all_access,
            "modules": user.effective_permissions(),
        })

    @action(detail=True, methods=["get", "put"], url_path="permissions")
    def set_permissions(self, request, pk=None):
        """
        GET/PUT /api/users/{id}/permissions/ — this person's DELTA from their team.

        Body: {"permissions": [{"module": "events", "can_view": true,
                                "can_create": null, ...}, ...]}

        null means INHERIT and is the default state of every cell. A module whose
        four cells are all null is not stored — an empty override row and no row
        mean the same thing, and keeping one would leave rows behind that read as
        "this person was singled out" when nobody was.

        Gated on the `roles` module, not `users`: deciding what somebody may do
        is a different job from editing their name, and it is the same right that
        governs a team's grid.
        """
        user = self.get_object()

        if request.method == "GET":
            return Response({
                "team_permissions": team_permission_matrix(user.team if user.team_id else None),
                "permission_overrides": UserPermissionSerializer(
                    user.permission_overrides.all(), many=True).data,
                "effective_permissions": user.effective_permissions(),
            })

        items = request.data if isinstance(request.data, list) else request.data.get("permissions", [])
        cleaned, error = _clean_permission_rows(items, allow_null=True)
        if error:
            return Response({"detail": error}, status=400)

        with transaction.atomic():
            user.permission_overrides.all().delete()
            UserPermission.objects.bulk_create([
                UserPermission(user=user, module=module, **cells)
                for module, cells in cleaned.items()
                # All-null carries no information; see the docstring.
                if any(v is not None for v in cells.values())
            ])

        user._effective_permissions = None
        return Response(UserListSerializer(user, context={"request": request}).data)
