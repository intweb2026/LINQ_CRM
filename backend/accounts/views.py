"""
accounts/views.py
──────────────────
User management — admin only.
"""
from django.contrib.auth import get_user_model, logout as django_logout
from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response

from .permissions import (
    IsAdminRole, assert_can_manage_user, assert_can_place_in_team,
    is_super_admin, managed_team_id,
)
from .serializers import (
    UserListSerializer, UserWriteSerializer, AssignEventsSerializer,
    UserPermissionSerializer, team_permission_matrix,
)
from .models import CRM_MODULES, PERM_ACTIONS, PERM_FIELDS, UserPermission, role_from_team_name
from .crm_permissions import crm_permission

from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.conf import settings as django_settings
from django.utils import timezone
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

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



class GoogleTokenLoginView(APIView):
    """
    POST /api/auth/google/ — the only way in.

    Body: {"credential": "<Google ID token>"}. The token is verified against
    Google, its email is matched to an ALREADY EXISTING active User, and the
    same DRF token payload the old login paths returned comes back. No user is
    created here: a Google account that nobody has provisioned is a 403, not a
    new row.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        credential = (request.data.get("credential") or "").strip()
        if not credential:
            return Response({"detail": "Google credential is required."}, status=400)

        client_id = django_settings.GOOGLE_OAUTH_CLIENT_ID
        if not client_id:
            return Response(
                {"detail": "Google Sign-In is not configured on this server."},
                status=500,
            )

        # 1. Verify the ID token with Google — signature, audience, expiry.
        try:
            idinfo = google_id_token.verify_oauth2_token(
                credential,
                google_requests.Request(),
                client_id,
            )
        except ValueError:
            return Response({"detail": "Invalid Google credential."}, status=401)

        # 2. Extract and validate email
        email = (idinfo.get("email") or "").strip().lower()
        if not email or not idinfo.get("email_verified"):
            return Response({"detail": "Google account email is not verified."}, status=401)

        # 3. Domain restriction
        allowed = django_settings.GOOGLE_OAUTH_ALLOWED_DOMAINS
        if allowed:
            domain = email.rsplit("@", 1)[-1]
            if domain not in allowed:
                return Response(
                    {"detail": "Sign-in is restricted to organisation accounts."},
                    status=403,
                )

        # 4. Match to an existing, active user who has login access
        try:
            user = User.objects.get(email__iexact=email, is_active=True, login_access=True)
        except User.DoesNotExist:
            return Response(
                {"detail": "No account with login access found for this email. Contact an administrator."},
                status=403,
            )

        # 5. Issue DRF token — identical shape to the retired login flows
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



class CustomAuthToken(ObtainAuthToken):
    """
    POST /api/auth/fallback/
    Hidden username/password fallback for emergency access when Google
    Sign-In is unavailable. Not linked from the main UI.

    Does NOT check login_access — this is a break-glass route. It checks
    only is_active (via Django's standard authenticate()).
    """
    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        token, _ = Token.objects.get_or_create(user=user)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        return Response({
            'token':    token.key,
            'user_id':  user.pk,
            'email':    user.email,
            'username': user.username,
            'role':     user.role,
        })


class LogoutView(APIView):
    """
    POST /api/auth/logout/ — revoke the caller's token. 204 on success.

    Logging out used to be purely client-side: the browser forgot the token and
    the row stayed valid forever, because DRF tokens carry no expiry. Anything
    that had read that string out of localStorage — a shared machine, a stale
    backup, a browser extension — kept full API access after the user believed
    they had signed out. Both sign-out paths now come through here: the Topbar
    button, and the six-hour inactivity timer in
    frontend/src/components/IdleLogout.jsx.

    ONE TOKEN PER USER. rest_framework.authtoken's model is a OneToOne, and
    login does Token.objects.get_or_create(user=user), so this signs the user
    out of every browser they are signed into rather than just this one. That is
    inherent to the token model, not a choice made here; per-device revocation
    would mean a token table with a device column (or knox), which is a
    migration, not a view. The next login simply mints a fresh key.

    Idempotent from the caller's point of view: filter().delete() on an already
    revoked token deletes nothing and still answers 204. A caller with no valid
    credential never reaches the body — IsAuthenticated answers 401 first, which
    is the same outcome the client wants.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        # ...and any Django session belonging to the same browser.
        # SessionAuthentication sits in DEFAULT_AUTHENTICATION_CLASSES next to
        # TokenAuthentication, so a sessionid cookie authenticates the whole API
        # on its own — and any staff member who has signed into /admin/ is
        # carrying one. Revoking only the token would leave that cookie a live
        # credential after the CRM had said goodbye, which is precisely the hole
        # an inactivity logout exists to close. A token-only caller has no
        # session to flush and this costs them nothing.
        django_logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserViewSet(viewsets.ModelViewSet):
    """CRUD + event assignment. Write actions require users-module permission."""
    permission_classes = [crm_permission("users")]
    # Everything UserListSerializer touches, fetched up front.
    #
    # MEASURED: 45 users cost 219 queries. Five separate per-row traversals, none
    # of them visible from the serializer's field list:
    #
    #   team.name / team.id      an unfetched FK        -> select_related
    #   mapped_lead              an unfetched FK        -> select_related
    #   permission_overrides     an unfetched reverse   -> prefetch_related
    #   team.permissions         read TWICE per row, by team_permission_matrix and
    #                            again inside effective_permissions()
    #                                                  -> prefetch_related
    #   assigned_events.count    a COUNT per row. `assigned_events` was already
    #                            prefetched, but a prefetch only serves .all();
    #                            .count() ignores it and goes back to the database.
    #                            The serializer now counts the prefetched list.
    #
    # This is the list behind the Users page, the Teams board, the role cards and
    # every user dropdown in the app, so it is on the critical path of most pages.
    queryset = (
        User.objects
        .select_related("team", "mapped_lead", "managed_team")
        .prefetch_related("assigned_events", "permission_overrides", "team__permissions")
        .order_by("-date_joined")
    )
    filterset_fields = ["role", "status", "team"]
    search_fields = ["username", "first_name", "last_name", "email"]

    def get_permissions(self):
        from rest_framework.permissions import IsAuthenticated
        # These actions are accessible to any authenticated user:
        # - my_permissions: every user must be able to fetch their own permission matrix
        # - list / retrieve: needed for user dropdowns throughout the app (e.g. MR assignment)
        # - role_stats: informational
        if self.action in (
            "list", "retrieve",
            "my_permissions", "role_stats",
        ):
            return [IsAuthenticated()]
        # sync_roles USED TO SIT IN THAT LIST, described as informational. It is
        # not: it rewrites the `role` column of every user in the database in one
        # POST, across every team, and User.save() turns role=admin into
        # is_superuser. So any authenticated session could re-role the whole
        # company, which is a direct-API route around the team-manager
        # restriction and around the users module itself. Nothing in the frontend
        # calls it; it is the escape hatch a super admin runs after RENAMING a
        # team, so it now answers to the same rule the rest of the admin surface
        # does.
        if self.action == "sync_roles":
            return [IsAdminRole()]
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

    def get_object(self):
        """
        THE ONE CHOKE POINT for "may this caller write to this account".

        Every mutating route on this viewset reaches its row through here —
        update, partial_update, destroy, and all seven detail @actions
        (assign_events, add_event, remove_event, move_team, toggle_status,
        reset_password, set_permissions). Putting the team-manager check in each
        of them instead would be nine copies of one rule, and the tenth action
        added later would be the one that forgot it.

        SAFE methods are deliberately not narrowed. /api/users/ has always been
        readable by any authenticated session — it is the directory behind the
        SCA picker, the ticket assignee list and the reporting-manager dropdown —
        so scoping a manager's READS would make a manager see LESS of it than the
        juniors they manage, while leaking nothing that was not already open. The
        Users PAGE narrows its own rows for a manager instead; the restriction
        that carries privilege is this one, and it is enforced here.
        """
        obj = super().get_object()
        if self.request.method not in SAFE_METHODS:
            assert_can_manage_user(self.request.user, obj)
        return obj

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
        # Both ENDS of the move. get_object() has already established that this
        # account is the caller's to touch; unassigning it, or sending it into
        # another team, is a write to a team they do not manage.
        assert_can_place_in_team(request.user, team_id)
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
        team = user.managed_team if user.managed_team_id else None
        return Response({
            "is_all_access": user.has_all_access,
            "modules": user.effective_permissions(),
            # The team this session manages, or null. The frontend needs the id
            # to narrow its Users page and to pin its Add-user form, and the name
            # so it can say WHICH team without a second request. Null for a super
            # admin even if they hold the column, matching managed_team_id() —
            # a super admin is not restricted to one team.
            "managed_team_id": None if is_super_admin(user) else user.managed_team_id,
            "managed_team_name": None if is_super_admin(user) or not team else team.name,
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
