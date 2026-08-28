from django.db import models
from django.utils.text import slugify
from django.conf import settings


class Team(models.Model):
    name        = models.CharField(max_length=100)
    slug        = models.SlugField(max_length=100, unique=True, blank=True)
    color       = models.CharField(max_length=20, blank=True, null=True, help_text="Hex code or CSS color name")
    description = models.TextField(blank=True, null=True)
    team_lead   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="led_teams",
    )
    is_all_access = models.BooleanField(
        default=False,
        help_text="Full access to every module, ignoring the permission rows below.",
    )
    is_archived = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table            = "teams"
        verbose_name        = "Team"
        verbose_name_plural = "Teams"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # `slug` is unique, and two teams may legitimately share a name ("Sales"
        # in two regions, say). Taking slugify(name) verbatim made the second one
        # raise IntegrityError out of the create endpoint — a 500, with nothing
        # in the response naming the collision. Suffix until it is free instead.
        if not self.slug:
            base = slugify(self.name) or "team"
            slug, n = base, 2
            while Team.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)


class TeamPermission(models.Model):
    """
    What a team can reach. The team IS the role.

    Access used to hang off a per-user CustomRole, which meant a team and a
    permission set were two things that had to be kept in step by hand — and in
    the live data they had already drifted, with four people in Sales Team
    holding the Speaker Sales set. There is now one answer per team, and a user
    inherits it by being in the team; per-person differences are recorded as
    deltas in accounts.UserPermission rather than as a second parallel hierarchy.

    Rows are keyed on the module strings in accounts.models.CRM_MODULES.
    """
    team       = models.ForeignKey(
        Team, on_delete=models.CASCADE, related_name="permissions"
    )
    module     = models.CharField(max_length=50)
    can_view   = models.BooleanField(default=False)
    can_create = models.BooleanField(default=False)
    can_update = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    # Row scope, not an action: on, this team sees every row in the module rather
    # than only the ones its assigned events cover. Only the modules in
    # accounts.models.SCOPED_MODULES read it.
    can_all    = models.BooleanField(default=False)

    class Meta:
        db_table        = "team_permissions"
        unique_together = [("team", "module")]
        ordering        = ["module"]

    def __str__(self):
        return f"{self.team} · {self.module}"


class TeamActivityLog(models.Model):
    class ActionType(models.TextChoices):
        MEMBER_MOVED   = "member_moved",   "Member Moved"
        MEMBER_REMOVED = "member_removed", "Member Removed"
        MEMBER_ADDED   = "member_added",   "Member Added"
        LEAD_ASSIGNED  = "lead_assigned",  "Lead Assigned"
        TEAM_RENAMED   = "team_renamed",   "Team Renamed"
        TEAM_DELETED   = "team_deleted",   "Team Deleted"
        TEAM_ARCHIVED  = "team_archived",  "Team Archived"
        TEAM_CREATED   = "team_created",   "Team Created"
        PERMISSIONS_CHANGED = "permissions_changed", "Permissions Changed"

    action_type      = models.CharField(max_length=30, choices=ActionType.choices)
    team             = models.ForeignKey(
        Team, on_delete=models.CASCADE, related_name="activity_logs"
    )
    user             = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="team_activity_subject",
    )
    moved_by         = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="team_activity_actor",
    )
    source_team      = models.ForeignKey(
        Team, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="activity_as_source",
    )
    destination_team = models.ForeignKey(
        Team, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="activity_as_destination",
    )
    notes      = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "team_activity_logs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action_type} — {self.team}"
