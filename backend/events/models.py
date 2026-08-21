"""
events/models.py
────────────────
Master event catalogue.
"""
from django.db import models
from django.utils import timezone


class Event(models.Model):
    class Status(models.TextChoices):
        DRAFT     = "Draft",     "Draft"
        UPCOMING  = "Upcoming",  "Upcoming"
        LIVE      = "Live",      "Live"
        COMPLETED = "Completed", "Completed"
        CANCELLED = "Cancelled", "Cancelled"
        POSTPONED = "Postponed", "Postponed"
        TBP       = "TBP",       "TBP"

    event_code  = models.CharField(max_length=50, unique=True, db_index=True)
    name        = models.CharField(max_length=255, blank=True, default="")
    # Provenance for the Zoho load. All rows written by ONE run of
    # `load_zoho_export` share one value, so a superseded or partial load can be
    # identified and deleted without a full database restore. Null on every row
    # that did not arrive through that command. Mirrors the same field on
    # PaperReview / ProposalSubmission.
    import_batch_id = models.UUIDField(null=True, blank=True, db_index=True)
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    city        = models.CharField(max_length=100, blank=True, default="")
    country     = models.CharField(max_length=100, blank=True, default="")
    venue       = models.CharField(max_length=255, blank=True, default="")
    event_date  = models.DateField()
    end_date    = models.DateField(null=True, blank=True)
    capacity    = models.PositiveIntegerField(default=500)
    expected_revenue = models.DecimalField(max_digits=14, decimal_places=2, default=0.00)

    # ── New fields from Events.csv ──────────────────────────────────────────
    master_code            = models.CharField(max_length=50, blank=True, default="", db_index=True)
    official_name          = models.CharField(max_length=255, blank=True, default="")
    spex_team              = models.CharField(max_length=255, blank=True, default="")
    tele_marketing_team    = models.CharField(max_length=255, blank=True, default="")
    market_research_team   = models.CharField(max_length=255, blank=True, default="")
    content_check          = models.CharField(max_length=255, blank=True, default="")
    marketing_check        = models.CharField(max_length=255, blank=True, default="")
    sales_check            = models.CharField(max_length=255, blank=True, default="")
    accepting_web_bookings = models.BooleanField(default=False)

    # ── 31 Fields Upgrade ──────────────────────────────────────────────────
    location               = models.CharField(max_length=255, blank=True, default="")
    website                = models.CharField(max_length=255, blank=True, default="")
    web_bookings           = models.BooleanField(default=False)
    nearest_related_event  = models.CharField(max_length=255, blank=True, default="")
    event_type             = models.CharField(max_length=100, blank=True, default="")
    website_live_date      = models.DateField(null=True, blank=True)
    vr1_sent_status        = models.CharField(max_length=100, blank=True, default="")
    sales_team             = models.CharField(max_length=255, blank=True, default="")
    team_leader            = models.CharField(max_length=255, blank=True, default="")
    telemarketing_team     = models.CharField(max_length=255, blank=True, default="")
    market_research_senior = models.CharField(max_length=255, blank=True, default="")
    market_research_junior = models.CharField(max_length=255, blank=True, default="")
    event_management_team  = models.CharField(max_length=255, blank=True, default="")
    official_event_name    = models.CharField(max_length=255, blank=True, default="")
    email_marketing_name   = models.CharField(max_length=255, blank=True, default="")
    branding_name          = models.CharField(max_length=255, blank=True, default="")
    annualisation          = models.CharField(max_length=100, blank=True, default="")
    date_format            = models.CharField(max_length=50, blank=True, default="")
    related_event_1        = models.CharField(max_length=100, blank=True, default="")
    related_event_2        = models.CharField(max_length=100, blank=True, default="")
    related_event_3        = models.CharField(max_length=100, blank=True, default="")
    upcoming_event_1       = models.CharField(max_length=100, blank=True, default="")
    upcoming_event_2       = models.CharField(max_length=100, blank=True, default="")
    upcoming_event_3       = models.CharField(max_length=100, blank=True, default="")

    sales_executive = models.ForeignKey(
        "accounts.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_events_list",
    )

    created_at  = models.DateTimeField(default=timezone.now)
    updated_at  = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Sync backward compatibility fields
        if self.official_event_name:
            self.name = self.official_event_name
            self.official_name = self.official_event_name
        else:
            self.name = self.event_code
            self.official_name = self.event_code
            
        self.accepting_web_bookings = self.web_bookings
        
        if self.location:
            self.city = self.location
            self.country = self.location
            self.venue = self.location
            
        self.tele_marketing_team = self.telemarketing_team
        self.market_research_team = self.market_research_senior

        # Sync sales_team and sales_executive
        if self.pk:
            orig = Event.objects.filter(pk=self.pk).first()
            if orig:
                if self.sales_executive != orig.sales_executive:
                    if self.sales_executive:
                        self.sales_team = self.sales_executive.get_full_name() or self.sales_executive.username
                    else:
                        self.sales_team = ""
                elif self.sales_team != orig.sales_team or (self.sales_team and not self.sales_executive):
                    if self.sales_team:
                        from django.contrib.auth import get_user_model
                        User = get_user_model()
                        name_str = self.sales_team.strip()
                        user = User.objects.filter(role='sales').filter(
                            models.Q(username__iexact=name_str) |
                            models.Q(email__iexact=name_str) |
                            models.Q(first_name__icontains=name_str) |
                            models.Q(last_name__icontains=name_str)
                        ).first()
                        if not user:
                            for u in User.objects.filter(role='sales'):
                                full = (u.get_full_name() or u.username).lower()
                                if name_str.lower() in full or full in name_str.lower():
                                    user = u
                                    break
                        self.sales_executive = user
                    else:
                        self.sales_executive = None
        else:
            if self.sales_executive and not self.sales_team:
                self.sales_team = self.sales_executive.get_full_name() or self.sales_executive.username
            elif self.sales_team and not self.sales_executive:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                name_str = self.sales_team.strip()
                user = User.objects.filter(role='sales').filter(
                    models.Q(username__iexact=name_str) |
                    models.Q(email__iexact=name_str) |
                    models.Q(first_name__icontains=name_str) |
                    models.Q(last_name__icontains=name_str)
                ).first()
                if not user:
                    for u in User.objects.filter(role='sales'):
                        full = (u.get_full_name() or u.username).lower()
                        if name_str.lower() in full or full in name_str.lower():
                            user = u
                            break
                self.sales_executive = user
        
        super().save(*args, **kwargs)

    class Meta:
        db_table = "events"
        ordering = ["-event_date"]
        indexes  = [
            models.Index(fields=["event_code"]),
            models.Index(fields=["event_date"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self):
        return f"{self.event_code} — {self.name}"

    @property
    def event_status(self):
        if not self.event_date:
            return "Live"
        from django.utils import timezone
        return "Completed" if self.event_date < timezone.now().date() else "Live"
