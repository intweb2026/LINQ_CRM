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
