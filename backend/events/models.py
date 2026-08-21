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

        # Sync sales_team and sales_executive. Every write path in the codebase
        # goes through save(), the importer and the bulk editor included
        # (accounts/bulk_update.py never uses queryset.update), so this one call
        # is what makes ANY new event route to the right person.
        self._sync_sales_owner()

        super().save(*args, **kwargs)

    def _sync_sales_owner(self):
        """
        Keep `sales_team`, the SCA name shown in the UI, and `sales_executive`,
        the FK that decides who can SEE this row, saying the same thing.

        Why the FK matters this much. events/views.py get_queryset scopes a non
        admin to `Q(assigned_users=user) | Q(sales_executive=user)`, and
        accounts.User.assigned_event_codes feeds the same pair to Bookings and
        Delegates. An event whose SCA text names somebody, but whose FK is null,
        is invisible to that person on every screen in the product, with nothing
        anywhere reporting a problem.

        Precedence, and the reason for each step:

          create   an explicitly passed FK wins and NAMES the text column;
                   otherwise the text is resolved. A create cannot be a "clear".
          update   1. FK moved by hand, so the text follows it. A human picking a
                      person from a dropdown outranks any string.
                   2. text emptied by hand, so the FK is cleared. This is how the
                      UI unassigns, and it must not be undone by step 4.
                   3. text names somebody and either changed or has no FK yet.
                      This is the case that was broken in production. A resolution
                      only ever ran at the moment the row was saved, so events
                      imported before a new starter existed kept a null FK
                      forever, and adding the account changed nothing.
                   4. FK set, text blank, so the text is filled in. Keeps the
                      Events table from showing an empty SCA on a row that is
                      correctly owned.

        An ambiguous or unknown name leaves the FK exactly as it was, and is
        logged. Guessing is what the old two way substring compare in this method
        did, and a guess here hands one person's events to another silently.
        """
        import logging

        from accounts.user_resolution import is_blank_name, resolve_owner

        log = logging.getLogger(__name__)

        def display(user):
            return user.get_full_name() or user.username

        def resolve_text():
            # The reason is left on the instance so a caller, an import report or
            # the routing command can say WHY a row was not routed, rather than
            # showing a blank owner with no explanation anywhere.
            user, reason = resolve_owner(self.sales_team)
            self.owner_routing = reason
            if user is not None:
                self.sales_executive = user
                self.sales_team = display(user)
            else:
                log.warning(
                    "event %s sales_team %r %s; sales_executive left as %s",
                    self.event_code, self.sales_team, reason,
                    self.sales_executive_id,
                )

        self.owner_routing = None
        orig = Event.objects.filter(pk=self.pk).first() if self.pk else None

        if orig is None:
            if self.sales_executive_id:
                self.sales_team = display(self.sales_executive)
            elif not is_blank_name(self.sales_team):
                resolve_text()
            return

        text_changed = (self.sales_team or "") != (orig.sales_team or "")

        if self.sales_executive_id != orig.sales_executive_id:
            self.sales_team = display(self.sales_executive) if self.sales_executive_id else ""
        elif text_changed and is_blank_name(self.sales_team):
            self.sales_executive = None
            self.sales_team = ""
        elif not is_blank_name(self.sales_team) and (text_changed or not self.sales_executive_id):
            resolve_text()
        elif self.sales_executive_id and is_blank_name(self.sales_team):
            self.sales_team = display(self.sales_executive)

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
