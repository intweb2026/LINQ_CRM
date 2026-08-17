"""
accounts/models.py
──────────────────
Custom user model with CRM roles and event assignments.
"""
import random
import string
from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

# Distinguishes "this row was loaded without its team column" from "this row has
# no team". Only the second is a real answer; the first must not be read as one.
_TEAM_NOT_LOADED = object()


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN           = "admin",           "Admin"
        SALES           = "sales",           "Sales"
        MARKET_RESEARCH = "market_research", "Market Research"
        SPEX            = "spex",            "SpEx"
        OPERATIONS      = "operations",      "Operations"
        SPEAKER_SALES   = "speaker_sales",   "Speaker Sales"
        TELEMARKETING   = "telemarketing",   "Telemarketing"
        DATA_MINING     = "data_mining",     "Data Mining"

    class Status(models.TextChoices):
        ACTIVE    = "active",    "Active"
        INACTIVE  = "inactive",  "Inactive"
        SUSPENDED = "suspended", "Suspended"

    role = models.CharField(
        max_length=20, choices=Role.choices, default=Role.SALES, db_index=True
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    is_team_lead = models.BooleanField(default=False, db_index=True)
    mapped_lead = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_members",
        db_index=True,
        help_text="The specific team lead this user/member is mapped under."
    )
    team = models.ForeignKey(
        "teams.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
        db_index=True
    )
    assigned_events = models.ManyToManyField(
        "events.Event",
        blank=True,
        related_name="assigned_users",
        help_text="Events accessible by this sales user. Ignored for admin.",
    )
    # Set to True by UserWriteSerializer when the request NAMED a role, i.e. when
    # a human picked one on the Add/Edit user form. Not a column: it describes one
    # request, not the row. See _should_derive_role().
    role_is_explicit = False

    class Meta:
        db_table = "users"

    def __str__(self):
        return f"{self.username} ({self.role})"

    @classmethod
    def from_db(cls, db, field_names, values):
        """
        Remember which team this row was loaded with.

        save() derives `role` from the team's name, and it used to do so on EVERY
        save. A role chosen by hand therefore survived only until the next
        unrelated write — a status toggle, a password reset, being made team lead
        — and then silently reverted to whatever the team name implied, with no
        request having asked for that. Deriving only on an actual team change
        needs the previous value, and this is where it is captured.
        """
        instance = super().from_db(db, field_names, values)
        instance._loaded_team_id = (
            instance.team_id if "team_id" in field_names else _TEAM_NOT_LOADED
        )
        return instance

    def _should_derive_role(self):
        """
        Should this save re-derive `role` from the team's name?

        Only when the placement actually changed and nobody said otherwise:

          * an explicit role from the form always wins, which is what makes the
            Role field on the Add/Edit user form genuinely editable;
          * a brand new row is a new placement, so it derives;
          * an existing row derives only if its team is not the one it was
            loaded with. A save that leaves the team alone leaves the role alone.

        A row loaded without its team column is treated as changed, which is the
        behaviour that was there before and cannot silently keep a stale role.
        """
        if self.role_is_explicit:
            return False
        if self._state.adding:
            return True
        loaded = getattr(self, "_loaded_team_id", _TEAM_NOT_LOADED)
        if loaded is _TEAM_NOT_LOADED:
            return True
        return loaded != self.team_id

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields", None)

        # Sync is_active with status
        if self.status == self.Status.ACTIVE:
            self.is_active = True
        else:
            self.is_active = False

        if update_fields is not None:
            update_fields = set(update_fields)
            if "status" in update_fields:
                update_fields.add("is_active")

        derive_role = self._should_derive_role()

        # Auto-sync role and permissions based on team assignment
        # If the user explicitly has the ADMIN role, ensure they have superuser/staff rights
        # regardless of their team.
        if self.role == self.Role.ADMIN:
            if not self.is_superuser or not self.is_staff:
                self.is_superuser = True
                self.is_staff = True
        elif self.team:
            derived = role_from_team_name(self.team.name)

            # The superuser grant is tied to the PROMOTION, not to the team's
            # name alone. Someone deliberately saved as Sales into a team called
            # "Admin Support" keeps the role that was asked for, and must not be
            # handed staff and superuser rights on the way past.
            if derive_role and derived == self.Role.ADMIN:
                self.is_superuser = True
                self.is_staff = True
                self.role = self.Role.ADMIN
            else:
                # Revoke superuser/staff if they are moved out of the Admin team and are not explicitly Admin role
                if self.is_superuser or self.is_staff:
                    self.is_superuser = False
                    self.is_staff = False

                if derive_role and derived and self.role != derived:
                    self.role = derived

            if update_fields is not None:
                update_fields.update(["is_superuser", "is_staff", "role"])

        if update_fields is not None:
            kwargs["update_fields"] = list(update_fields)

        super().save(*args, **kwargs)
        # This row's team is now the stored one, so a second save in the same
        # request must not see the pre-save value and derive all over again.
        self._loaded_team_id = self.team_id

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_sales(self):
        return self.role == self.Role.SALES

    def assigned_event_codes(self):
        """
        The event codes this user may see, or None for an admin, meaning
        unrestricted.

        Reads BOTH of the ways an event can belong to somebody, because there are
        two of them and their names are one character apart. `assigned_events` is
        the M2M declared above, whose reverse accessor on Event is
        `assigned_users`. `Event.sales_executive` is a separate FK, whose reverse
        accessor on this model is the near-identical `assigned_events_list`.

        Only the M2M used to be read here, and on the current database it is
        empty on all 217 events while sales_executive is set on most of them. So
        this returned [] for every one of the 42 non-admin accounts, and
        RBACMixin.rbac_filter turns an empty list into `qs.none()`; the Bookings
        page was empty for everybody who is not an admin, including the sales
        people looking at their own invoices.

        The Events module has always resolved ownership the other way, as
        `Q(assigned_users=user) | Q(sales_executive=user)` in events/views.py, and
        that is the set the New Booking event dropdown offers. Returning the same
        set here is what makes the events a person can file a booking against and
        the bookings they can then see be one list rather than two.
        """
        if self.is_admin:
            return None
        # Imported here, not at module scope: events.models imports this model.
        from django.db.models import Q
        from events.models import Event
        return list(
            Event.objects
            .filter(Q(assigned_users=self) | Q(sales_executive=self))
            # A blank code would scope to `event_code = ''`, which is every row
            # that never got one rather than no rows at all.
            .exclude(event_code="")
            .values_list("event_code", flat=True)
            .distinct()
        )

    @property
    def has_all_access(self):
        """Full access to everything, by account or by team."""
        # Imported here rather than at module scope: permissions.py pulls in
        # rest_framework, and models.py is loaded during app registry setup.
        from .permissions import HP_USERNAME
        if self.username == HP_USERNAME:
            return True
        return bool(self.team_id and self.team and self.team.is_all_access)

    def effective_permissions(self):
        """
        {module: {view, create, update, delete}} — the team's grid with this
        person's overrides applied on top.

        THE resolution, in one place. Every gate in the codebase reads it, so
        "why can this person do that" has exactly one answer to trace: their team
        grants it, or they were singled out for it.

        Memoised on the instance because DRF builds the request user once and
        then asks per view, per object; without this a single list request
        re-ran both queries for every permission check it made.
        """
        cached = getattr(self, "_effective_permissions", None)
        if cached is not None:
            return cached

        if self.has_all_access:
            resolved = {m: {a: True for a in PERM_ACTIONS} for m in CRM_MODULES}
            self._effective_permissions = resolved
            return resolved

        resolved = {m: {a: False for a in PERM_ACTIONS} for m in CRM_MODULES}

        if self.team_id:
            for row in self.team.permissions.all():
                if row.module in resolved:
                    resolved[row.module] = {
                        a: bool(getattr(row, f"can_{a}")) for a in PERM_ACTIONS
                    }

        # None means "inherit", so only a real True/False is written through.
        # Testing truthiness here would turn every inherit into a revoke.
        for row in self.permission_overrides.all():
            if row.module not in resolved:
                continue
            for action in PERM_ACTIONS:
                override = getattr(row, f"can_{action}")
                if override is not None:
                    resolved[row.module][action] = override

        self._effective_permissions = resolved
        return resolved


# ── Team name → role ─────────────────────────────────────────────────────────
# ORDER IS THE BEHAVIOUR here, not formatting. The FIRST keyword found in the
# name wins, so "Telesales" resolves to Telemarketing through "tele" before
# "sales" is ever considered, and "Speaker Sales Ops" resolves to Operations
# through "ops". Reordering these rows silently re-roles people.
#
# Declared once and read by User.save(), by UserViewSet.sync_roles, and mirrored
# in frontend/src/lib/roleFromTeam.js so the Add user form can show the role a
# team is about to imply BEFORE the save rather than after it. The three copies
# used to be three hand-written if/elif chains; accounts/tests_wire_probe.py now
# asserts the JavaScript one still agrees with this one, name for name.
TEAM_NAME_ROLE_KEYWORDS = [
    ("admin",           User.Role.ADMIN),
    ("market research", User.Role.MARKET_RESEARCH),
    ("data mining",     User.Role.DATA_MINING),
    ("dmd",             User.Role.DATA_MINING),
    ("spex",            User.Role.SPEX),
    ("operation",       User.Role.OPERATIONS),
    ("ops",             User.Role.OPERATIONS),
    ("speaker sales",   User.Role.SPEAKER_SALES),
    ("telemarketing",   User.Role.TELEMARKETING),
    ("tele marketing",  User.Role.TELEMARKETING),
    ("tele",            User.Role.TELEMARKETING),
    ("sales",           User.Role.SALES),
]


def role_from_team_name(team_name):
    """The role a team's name implies, or None when no keyword matches."""
    haystack = (team_name or "").lower().strip()
    if not haystack:
        return None
    for keyword, role in TEAM_NAME_ROLE_KEYWORDS:
        if keyword in haystack:
            return role
    return None


class ActionLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="action_logs")
    action = models.CharField(max_length=255)
    details = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "action_logs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} - {self.action} at {self.created_at}"


class OTPToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="otp_tokens")
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "otp_tokens"
        indexes = [models.Index(fields=["user", "otp"])]

    def __str__(self):
        return f"OTP for {self.user.email} ({'used' if self.is_used else 'active'})"

    def is_expired(self):
        return timezone.now() > self.expires_at

    @classmethod
    def create_for_user(cls, user):
        cls.objects.filter(user=user, is_used=False).update(is_used=True)
        otp = "".join(random.choices(string.digits, k=6))
        return cls.objects.create(
            user=user,
            otp=otp,
            expires_at=timezone.now() + timedelta(minutes=5),
        )


CRM_MODULES = [
    "bookings", "ticket_central", "events", "reports",
    "users", "teams", "performance", "webhooks", "roles",
    # Placeholder pipeline modules. Registered so roles can be configured
    # ahead of the real feature; every existing role is backfilled all-False
    # by migration 0020, so nothing is visible until it is granted.
    "paper_review", "proposal_submission",
]

PERM_ACTIONS = ("view", "create", "update", "delete")
PERM_FIELDS = tuple(f"can_{a}" for a in PERM_ACTIONS)


class UserPermission(models.Model):
    """
    One person's DIFFERENCE from their team, per module.

    NOT a second permission set. Each of the four cells is three-state, and the
    third state is the important one:

        None   inherit whatever the team says, now and after the team changes
        True   granted to this person on top of the team
        False  taken away from this person, even though the team has it

    Storing the delta rather than the whole matrix is what makes inheritance
    real: widen a team's access tomorrow and everyone in it widens with it,
    except on the exact cells someone was deliberately singled out for. A copy of
    the effective matrix would silently freeze each person at the day they were
    edited.

    A row with all four cells None carries no information and is deleted rather
    than stored — see UserViewSet.set_permissions.
    """
    user       = models.ForeignKey(
        "User", on_delete=models.CASCADE, related_name="permission_overrides"
    )
    module     = models.CharField(max_length=50)
    can_view   = models.BooleanField(null=True, default=None)
    can_create = models.BooleanField(null=True, default=None)
    can_update = models.BooleanField(null=True, default=None)
    can_delete = models.BooleanField(null=True, default=None)

    class Meta:
        db_table        = "user_permissions"
        unique_together = [("user", "module")]
        ordering        = ["module"]

    def __str__(self):
        return f"{self.user} · {self.module}"

    def is_empty(self):
        return all(getattr(self, f) is None for f in PERM_FIELDS)
