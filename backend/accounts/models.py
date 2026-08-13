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
    custom_role = models.ForeignKey(
        "CustomRole",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="users",
        help_text="The permission set. THIS is what decides module access — see "
                  "accounts/crm_permissions.py. `role` above is a job-function "
                  "label and grants nothing on its own.",
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
        """Returns list of event_codes or None (admin = unrestricted)."""
        if self.is_admin:
            return None
        return list(self.assigned_events.values_list("event_code", flat=True))


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


class CustomRole(models.Model):
    """Admin-defined roles. All permissions are managed via RolePermission entries."""
    name           = models.CharField(max_length=50, unique=True)
    display_label  = models.CharField(max_length=50)
    color          = models.CharField(max_length=20, default="#6b7280")
    description    = models.TextField(blank=True, default="")
    is_all_access  = models.BooleanField(default=False, help_text="If True, grants full access to all modules.")
    is_system_role = models.BooleanField(default=False, help_text="Pre-seeded system role — cannot be deleted.")
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "custom_roles"
        ordering = ["display_label"]

    def __str__(self):
        return self.display_label


CRM_MODULES = [
    "bookings", "ticket_central", "events", "reports",
    "users", "teams", "performance", "webhooks", "roles",
    # Placeholder pipeline modules. Registered so roles can be configured
    # ahead of the real feature; every existing role is backfilled all-False
    # by migration 0020, so nothing is visible until it is granted.
    "paper_review", "proposal_submission",
]

class RolePermission(models.Model):
    """Per-module CRUD permissions for a CustomRole."""
    custom_role = models.ForeignKey(
        CustomRole, on_delete=models.CASCADE, related_name="permissions"
    )
    module      = models.CharField(max_length=50)
    can_view    = models.BooleanField(default=False)
    can_create  = models.BooleanField(default=False)
    can_update  = models.BooleanField(default=False)
    can_delete  = models.BooleanField(default=False)

    class Meta:
        db_table         = "role_permissions"
        unique_together  = [("custom_role", "module")]
        ordering         = ["module"]

    def __str__(self):
        return f"{self.custom_role} · {self.module}"
