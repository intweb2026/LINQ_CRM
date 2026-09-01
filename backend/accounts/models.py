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
    login_access = models.BooleanField(
        default=True,
        db_index=True,
        help_text="When unchecked the user exists in the system but cannot sign in.",
    )
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
    managed_team = models.ForeignKey(
        "teams.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managers",
        db_index=True,
        help_text=(
            "The team this person MANAGES. Set only by a super admin. It opens the "
            "Users module for them and pins every write they make to this one team."
        ),
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

    @property
    def is_team_manager(self):
        """
        Holds Manager rights over one team.

        DELIBERATELY NOT `is_team_lead`, and not `Team.team_lead`. Those two are
        about DATA — who a member reports to, whose bookings a lead may read (see
        data_scope_user_ids) — and every existing scope rule reads them. Manager
        rights are about ADMINISTERING PEOPLE, and answering both questions with
        one flag would have handed every existing team lead in the database the
        ability to create and delete accounts the moment this shipped.
        """
        return self.managed_team_id is not None

    def assigned_event_codes(self):
        """
        The event codes belonging to THIS PERSON, or None for an admin, meaning
        unrestricted.

        Personal on purpose. Read `visible_event_codes` for what a caller may
        SEE, which is this set widened to everyone who reports to them when the
        caller is a lead. The two were one method until data sharing gained the
        reporting-manager rule, and keeping them apart is what stops a lead's own
        assignment card from reporting their reports' catalogue as their own.

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
        return self._event_codes_owned_by([self.pk])

    def _event_codes_owned_by(self, user_ids):
        """
        The codes of every event owned by any of `user_ids`, by either route.

        Factored out because two callers need the same pair of ownership routes
        over different sets of people; `assigned_event_codes` asks about one
        person, `visible_event_codes` asks about a lead and everyone who
        reports to them.
        """
        if not user_ids:
            return []
        # Imported here, not at module scope: events.models imports this model.
        from django.db.models import Q
        from events.models import Event
        return list(
            Event.objects
            .filter(Q(assigned_users__in=user_ids) | Q(sales_executive__in=user_ids))
            # A blank code would scope to `event_code = ''`, which is every row
            # that never got one rather than no rows at all.
            .exclude(event_code="")
            .values_list("event_code", flat=True)
            .distinct()
        )

    def data_scope_user_ids(self):
        """
        The people whose rows this user may reach, as a list of ids, or None for
        an admin, meaning unrestricted.

        Everybody gets themselves. A lead also gets everyone who NAMES THEM as
        their reporting manager, which is `mapped_lead`, "the specific team lead
        this user is mapped under".

        THE REPORTING MANAGER IS THE WHOLE RULE
        Not team membership, and not the `is_team_lead` flag. Two consequences
        worth stating, because both are intended:

          * Two leads sitting in one team see DIFFERENT sets, one per manager.
            Terry sees the people mapped to Terry and nobody else, even a
            colleague of theirs in the same team who is mapped to Fred.
          * A member whose reporting manager is BLANK belongs to nobody, so their
            rows stay private to them. Sharing is opt in, one filled-in field at
            a time, and an unmapped account cannot leak by sitting in a team.

        Being flagged `is_team_lead` grants nothing on its own; a lead with
        nobody mapped under them sees only their own rows. That is why the flag
        is not consulted here: anyone named as somebody's `mapped_lead` IS their
        lead by definition, so a second gate could only ever disagree with the
        field and hide rows the mapping says to share.

        Members are filtered to ACTIVE. An inactive account cannot sign in, so
        its rows would otherwise be visible to the lead and to nobody else,
        which is a quieter form of the orphaning this whole scope prevents.
        """
        if self.is_admin:
            return None
        ids = {self.pk}
        ids.update(
            User.objects
            .filter(mapped_lead=self, status=self.Status.ACTIVE)
            .values_list("pk", flat=True)
        )
        return list(ids)

    def visible_event_codes(self):
        """
        The event codes this user may SEE, or None for an admin.

        `assigned_event_codes` deliberately stays personal: it answers "which
        events are this person's own", which is what the per-user events_stats
        card on the Users page reports, and widening it would have made a lead's
        own assignment card silently report their reports' catalogue too.

        This is that same question asked over `data_scope_user_ids`, so a lead's
        Bookings and Events grids cover the people mapped under them while every
        other caller of the personal accessor is untouched.
        """
        if self.is_admin:
            return None
        return self._event_codes_owned_by(self.data_scope_user_ids())

    @property
    def has_all_access(self):
        """Full access to everything, by account or by team."""
        # Imported here rather than at module scope: permissions.py pulls in
        # rest_framework, and models.py is loaded during app registry setup.
        from .permissions import dapi_USERNAME
        if self.username == dapi_USERNAME:
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

        resolved = {m: {a: False for a in PERM_ACTIONS} for m in CRM_MODULES}

        if self.team_id:
            for row in self.team.permissions.all():
                if row.module in resolved:
                    resolved[row.module] = {
                        a: bool(getattr(row, f"can_{a}")) for a in PERM_ACTIONS
                    }

        # MANAGER RIGHTS OPEN THE USERS MODULE, whatever the team's grid says.
        #
        # Assigning somebody a team to manage has to be enough on its own. Making
        # it a two-step — hand them the team here, then remember to tick four
        # boxes on their team's grid over there — grants the right to nobody the
        # first time it is used, and ticking those boxes on the TEAM would hand
        # the same four to every other member of it.
        #
        # `teams` view rides along because the Users screen renders team names
        # and its form offers a team; a manager who could not read /api/teams/
        # would get a Users page with an empty Team column. View only: every
        # write action on TeamViewSet asks for teams.update, so the manager still
        # cannot rename, archive, or move members between teams.
        #
        # Applied BEFORE the deltas below, so a super admin can still take one of
        # these back from one person — a manager who may not delete accounts is
        # `can_delete: false` on their users row, and that keeps working.
        #
        # `roles` is NOT granted, and that is the line between a manager and a
        # super admin: deciding what a team MAY DO stays with whoever holds the
        # permission grid. See UserViewSet.set_permissions.
        if self.managed_team_id:
            resolved["users"].update(
                view=True, create=True, update=True, delete=True,
            )
            resolved["teams"]["view"] = True

        # None means "inherit", so only a real True/False is written through.
        # Testing truthiness here would turn every inherit into a revoke.
        for row in self.permission_overrides.all():
            if row.module not in resolved:
                continue
            for action in PERM_ACTIONS:
                override = getattr(row, f"can_{action}")
                if override is not None:
                    resolved[row.module][action] = override

        # Full access, applied LAST so no grid row can take it away — and applied
        # to the four ACTIONS only.
        #
        # "all" is left as the grid stored it, on purpose. It is row scope, not a
        # capability, and this codebase has always kept the two apart for
        # bookings and events: an is_all_access team passes every module gate
        # while rbac_filter still narrows it to that person's assigned events,
        # and only role=admin bypasses that. accounts/tests_write_scoping.py
        # exists because a caller who was is_all_access AND still scoped could
        # once delete rows they could not read. Blanket-Truing this cell here
        # would hand that same caller every booking in the database as a side
        # effect of a feature about sharing one module.
        if self.has_all_access:
            for cells in resolved.values():
                for action in PERM_ACTIONS:
                    if action != "all":
                        cells[action] = True

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
        # This table had exactly one index in the database — its primary key. Both
        # of its read patterns are date-ordered: the recent-activity feed over the
        # whole table, and one person's history. The composite leads with user_id
        # so it also serves the plain FK lookup, which is why no separate
        # single-column user_id index is created.
        indexes = [
            models.Index(
                fields=["user", "-created_at"], name="action_logs_user_created_idx",
            ),
            models.Index(fields=["-created_at"], name="action_logs_created_idx"),
        ]

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
    # "reports" was here until the Reports page was removed. Its rows are deleted
    # by migration 0026; the three Dashboard sections it used to gate are booking
    # aggregates and now read "bookings".
    "bookings", "ticket_central", "events",
    "users", "teams", "performance", "webhooks", "roles",
    # Google Sync. Was reached through "webhooks" — the two share nothing but a
    # sidebar group, and one grid cell could not say "may manage sheet pushes"
    # without also saying "may replay webhook deliveries". Backfilled all-False
    # by migration 0027, so no team gains it by the split.
    "google_sync",
    # Placeholder pipeline modules. Registered so roles can be configured
    # ahead of the real feature; every existing role is backfilled all-False
    # by migration 0020, so nothing is visible until it is granted.
    "paper_review", "proposal_submission",
]

# "all" is not a verb like the other four. The first four say whether a module
# OPENS; "all" says whether the rows inside it are the caller's own or every row
# there is. It rides in this tuple anyway because every layer between the
# database and the checkbox — the two permission models, the resolver below, the
# serializers, the PUT validators, the grid and its delta — walks PERM_ACTIONS
# generically, so a fifth entry reaches all of them without a fifth copy of the
# loop. Read by paper_review/access.py, proposal_submission/access.py,
# RBACMixin.rbac_filter and EventViewSet.get_queryset, and by nothing else:
# crm_permission never maps a request onto it, so ticking it on an unscoped
# module grants nothing.
PERM_ACTIONS = ("view", "create", "update", "delete", "all")
PERM_FIELDS = tuple(f"can_{a}" for a in PERM_ACTIONS)

# The modules whose querysets are row-scoped, and therefore the only ones where
# can_all changes anything. Mirrored by `scoped` in frontend lib/constants.js,
# which greys the cell out everywhere else rather than offering a tick that does
# nothing.
SCOPED_MODULES = frozenset({
    "bookings", "events", "paper_review", "proposal_submission",
})


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
    # Three-state like the rest: None inherits the team's row scope, True hands
    # this one person every row in the module, False pins them back to their own
    # rows even where the team sees everything.
    can_all    = models.BooleanField(null=True, default=None)

    class Meta:
        db_table        = "user_permissions"
        unique_together = [("user", "module")]
        ordering        = ["module"]

    def __str__(self):
        return f"{self.user} · {self.module}"

    def is_empty(self):
        return all(getattr(self, f) is None for f in PERM_FIELDS)
