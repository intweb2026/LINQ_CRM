"""
accounts/views.py
──────────────────
User management — admin only.
"""
from django.contrib.auth import get_user_model
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .permissions import IsAdminRole
from .serializers import UserListSerializer, UserWriteSerializer, AssignEventsSerializer, CustomRoleSerializer, RolePermissionSerializer
from .models import CustomRole, RolePermission, CRM_MODULES, role_from_team_name
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
    filterset_fields = ["role", "status", "team", "custom_role"]
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
        """GET /api/users/my-permissions/ — returns the current user's full permission matrix."""
        user = request.user

        # HP gets full access to everything
        if user.username == "HP":
            all_perms = {m: {"view": True, "create": True, "update": True, "delete": True} for m in CRM_MODULES}
            return Response({"is_all_access": True, "modules": all_perms})

        custom_role = getattr(user, "custom_role", None)
        if not custom_role:
            return Response({"is_all_access": False, "modules": {}})

        if custom_role.is_all_access:
            all_perms = {m: {"view": True, "create": True, "update": True, "delete": True} for m in CRM_MODULES}
            return Response({"is_all_access": True, "modules": all_perms})

        modules = {}
        for perm in custom_role.permissions.all():
            modules[perm.module] = {
                "view":   perm.can_view,
                "create": perm.can_create,
                "update": perm.can_update,
                "delete": perm.can_delete,
            }
        return Response({"is_all_access": False, "modules": modules})


class CustomRoleViewSet(viewsets.ModelViewSet):
    """Admin-only CRUD for roles."""
    permission_classes = [IsAdminRole]
    queryset           = CustomRole.objects.prefetch_related("permissions").all()
    serializer_class   = CustomRoleSerializer

    def destroy(self, request, *args, **kwargs):
        """Allow deletion of any role. Users assigned to it will have custom_role set to NULL."""
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["put"], url_path="permissions")
    def set_permissions(self, request, pk=None):
        """
        PUT /api/roles/{id}/permissions/
        Body: [{"module": "events", "can_view": true, "can_create": false, ...}, ...]
        Replaces all permissions for this role.
        """
        role = self.get_object()
        items = request.data if isinstance(request.data, list) else request.data.get("permissions", [])

        # Validate
        valid_modules = set(CRM_MODULES)
        seen = set()
        for item in items:
            m = item.get("module")
            if m not in valid_modules:
                return Response({"detail": f"Unknown module: {m}"}, status=400)
            if m in seen:
                return Response({"detail": f"Duplicate module: {m}"}, status=400)
            seen.add(m)

        # Upsert permissions
        for item in items:
            RolePermission.objects.update_or_create(
                custom_role=role,
                module=item["module"],
                defaults={
                    "can_view":   bool(item.get("can_view",   False)),
                    "can_create": bool(item.get("can_create", False)),
                    "can_update": bool(item.get("can_update", False)),
                    "can_delete": bool(item.get("can_delete", False)),
                },
            )

        # If is_all_access changed, update it
        if "is_all_access" in request.data:
            role.is_all_access = bool(request.data["is_all_access"])
            role.save(update_fields=["is_all_access"])

        # Return updated role
        role.refresh_from_db()
        return Response(CustomRoleSerializer(role).data)
