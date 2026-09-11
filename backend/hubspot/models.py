"""
hubspot/models.py
──────────────────
The cache HubSpot data is read from. Nothing in the CRM reads HubSpot live.

Both tables are caches, not records: every row can be rebuilt by re-running its
sync command, and losing one costs a refetch and nothing else. That is why
neither carries an FK into the CRM. A contact row is keyed on the email address
because that is what we have on a booking, and a call row is keyed on HubSpot's
own call id because that is what makes a re-fetch idempotent.

`fetched_at` on both is load-bearing rather than decorative. It drives the
staleness check that decides what the next run bothers to fetch, and it is what
the UI shows beside a figure so a number a few hours old reads as a number a
few hours old rather than as the truth.
"""
from django.db import models
from django.utils import timezone


class HubSpotContact(models.Model):
    """
    One HubSpot contact, as much of it as the CRM displays.

    Keyed on lowercased email. HubSpot matches emails case insensitively but
    echoes back the case it stored, so normalising on write is what keeps one
    person from occupying two rows.
    """
    email = models.EmailField(primary_key=True)
    contact_id = models.CharField(max_length=50, blank=True, default="", db_index=True)

    phone = models.CharField(max_length=50, blank=True, default="")
    mobile_phone = models.CharField(max_length=50, blank=True, default="")
    direct_phone = models.CharField(max_length=50, blank=True, default="")

    # Total calls HubSpot has associated with this contact. Null means "not
    # known", which is NOT the same as zero and must not render as zero: a
    # contact whose count has never been fetched, or whose fetch failed, has to
    # look different from one that has genuinely never been called.
    times_called = models.IntegerField(null=True, blank=True)
    times_called_at = models.DateTimeField(null=True, blank=True)

    # The most recent call, and how long it lasted. Read in the SAME request
    # that counts the calls, because the calls search returns the newest row
    # alongside its total; asking twice would double the cost of the only part
    # of this sync that is one request per contact.
    #
    # A duration of 0 is meaningful and is NOT null: it is a call that connected
    # to nothing, which is exactly what a caller wants to see before dialling
    # again. Null means we have never looked.
    last_call_at = models.DateTimeField(null=True, blank=True)
    last_call_seconds = models.IntegerField(null=True, blank=True)

    fetched_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "hubspot_contacts"
        indexes = [models.Index(fields=["fetched_at"])]

    def __str__(self):
        return self.email

    @property
    def best_phone(self) -> str:
        """
        The number to try first: direct, then mobile, then the main line.

        Credit control calls a person, so a direct line beats a switchboard.
        Spelled here rather than in a serializer because both the queue and the
        drill-down want the same answer.
        """
        return self.direct_phone or self.mobile_phone or self.phone


class HubSpotCall(models.Model):
    """
    One logged call, reduced to the four things the dashboard counts by.

    `shift_date` is the UTC calendar date of the call, and that is not a
    shortcut. The callers' shift runs 6:30pm to 3:30am IST, which is 13:00 to
    22:00 UTC, so a shift that crosses midnight in IST sits entirely inside one
    UTC day. Bucketing on the UTC date therefore gives exactly one column per
    shift with no cutoff hour to tune, and nothing that breaks twice a year when
    North America changes its clocks. The date also equals the IST evening the
    shift began, which is how the column is labelled.
    """
    call_id = models.CharField(max_length=50, primary_key=True)
    owner_id = models.CharField(max_length=50, blank=True, default="", db_index=True)
    # The owner's email, resolved from HubSpot's owners endpoint once per sync
    # and stored here. It is what the dashboard matches a CRM user on, and it is
    # stored rather than looked up because the dashboard may never call HubSpot.
    # Matching on EMAIL rather than on a hard-coded id table means adding
    # somebody to the Credit Control team is the only step when the people
    # change; the predecessor build carried a rep-name-to-owner-id constant that
    # was a second roster to keep in step.
    owner_email = models.EmailField(blank=True, default="", db_index=True)
    occurred_at = models.DateTimeField(db_index=True)
    shift_date = models.DateField(db_index=True)
    disposition = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        db_table = "hubspot_calls"
        indexes = [models.Index(fields=["shift_date", "owner_id"])]

    def __str__(self):
        return f"{self.call_id} @ {self.occurred_at:%Y-%m-%d %H:%M}"


class HubSpotPurposeCount(models.Model):
    """
    How many contacts sit under one `contact_purpose` code, and how many of
    those are mailable.

    Ticket Central's Mining Matrix reads this, one row per event code. It is a
    cache like everything else here: the counts move as marketing works the
    list, and a figure a few hours old with the time beside it is worth far more
    than a page that waits on HubSpot to render.

    ONE REQUEST PER CODE, NOT ONE PER CONTACT. The contacts search returns a
    `total` next to the page, so asking for a single result and reading the
    total answers a count of ninety thousand contacts in one call. `mailable` is
    tested with HAS_PROPERTY rather than against a value, because the property
    is an enumeration with a single option and what is being asked is whether
    marketing has classified the contact at all.
    """
    purpose = models.CharField(max_length=100, primary_key=True)
    mailable_count = models.IntegerField(default=0)
    total_count = models.IntegerField(default=0)
    fetched_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "hubspot_purpose_counts"
        ordering = ["purpose"]

    def __str__(self):
        return f"{self.purpose}: {self.mailable_count} mailable of {self.total_count}"
