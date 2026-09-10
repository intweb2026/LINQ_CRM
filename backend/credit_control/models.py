"""
credit_control/models.py
─────────────────────────
Three tables. Everything else the module shows is derived at read time from
book_events, book_delegates and events, which already hold it.

WHAT IS DELIBERATELY NOT HERE. The Apps Script build this replaces carried a
hidden _Snapshot of the source sheet, because a spreadsheet has no join. There
is no snapshot here: the invoice is a row in book_events and reading it live is
both cheaper and impossible to get out of step. Nor is there a roster table;
the Credit Control team in `teams` is the roster, its `team_lead` is the
first-touch caller and its other members are the handoff pool.

WHAT IS HERE is the work the callers do, which exists nowhere else: who owns a
lead, what was said, and what the classifier decided about a speaker invoice.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from . import constants


class CreditControlDisposition(models.Model):
    """
    The dropdown, owned by whoever runs the team rather than by a deploy.

    `category` drives the engine and `status_group` drives the dashboard; see
    constants.py for why those are two columns and not one. `active` retires a
    value without deleting it, because leads already carry it and a deleted row
    would leave those leads reporting a value the UI cannot explain.
    """
    label = models.CharField(max_length=100, unique=True)
    category = models.CharField(
        max_length=20,
        choices=[(c, c) for c in constants.CATEGORIES],
        default=constants.CATEGORY_NOT_REACHED,
    )
    status_group = models.CharField(
        max_length=30,
        choices=[(g, g) for g in constants.STATUS_GROUPS],
        default=constants.GROUP_ATTEMPTED,
    )
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "credit_control_dispositions"
        ordering = ["sort_order", "label"]

    def __str__(self):
        return self.label

    @property
    def is_sticky(self) -> bool:
        """Does this disposition pin its lead to the current owner? See the engine."""
        return self.category in constants.STICKY_CATEGORIES

    @classmethod
    def seed(cls) -> int:
        """
        Write the seed list, once, without touching anything already there.

        get_or_create rather than update_or_create on purpose: a label whose
        category or group has been edited in the UI keeps the edit. Returns how
        many rows it created, so a command can report "already seeded".
        """
        created = 0
        for index, (label, category, group) in enumerate(constants.DISPOSITION_SEED):
            _, made = cls.objects.get_or_create(
                label=label,
                defaults={
                    "category": category,
                    "status_group": group,
                    "sort_order": index * 10,
                },
            )
            created += int(made)
        return created


class CreditControlLead(models.Model):
    """
    One invoice being chased, and the caller's work on it.

    KEYED ON THE INVOICE NUMBER, which is already unique in book_events. One
    invoice is one phone call however many delegates sit under it, so the
    delegates are read through the FK for display and never duplicate a lead.

    `bucket` is derived by the engine on every refresh and stored, not computed
    at read time. Storing it is what lets the queue be one indexed query instead
    of a re-derivation per request, and the engine is the only writer.

    A LEAD IS NEVER DELETED. A row that leaves the pending pull becomes
    bucket=DONE and keeps its remarks; a row that turns out to be uninvoiced
    becomes NOT_INVOICED and keeps them too. Deleting would throw away the only
    record that anybody ever called this company.
    """

    class Bucket(models.TextChoices):
        ACTIVE = "active", "Active"
        NOT_INVOICED = "not_invoiced", "Not invoiced yet"
        SPEX = "spex", "SpEx & Speaker Table"
        DONE = "done", "Done"

    class InvoiceType(models.TextChoices):
        BLIND = constants.INVOICE_TYPE_BLIND, constants.INVOICE_TYPE_BLIND
        REQUESTED = constants.INVOICE_TYPE_REQUESTED, constants.INVOICE_TYPE_REQUESTED

    class TypeSource(models.TextChoices):
        RULE = "rule", "Deterministic rule"
        MODEL = "model", "Classifier"
        MANUAL = "manual", "Human override"

    invoice = models.OneToOneField(
        "book_event.BookEvent",
        on_delete=models.CASCADE,
        related_name="credit_control",
        to_field="invoice_number",
        db_column="invoice_number",
        # Matches book_delegate.BookDelegate.invoice: the FK is on a natural key
        # and the constraint is left off so a load that rewrites book_events can
        # run without ordering itself around this table.
        db_constraint=False,
        primary_key=True,
    )

    bucket = models.CharField(
        max_length=20, choices=Bucket.choices, default=Bucket.ACTIVE, db_index=True,
    )

    # ── Ownership ──────────────────────────────────────────────────────────
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="credit_control_leads",
        db_index=True,
    )
    # When this lead first entered the active flow. The handoff fork reads it
    # and NOT the invoice date, which is what stops a backdated invoice being
    # handed off on the very run that created it: a caller has to get at least
    # one shift with a lead before it can move.
    first_seen = models.DateTimeField(null=True, blank=True)
    handed_off_at = models.DateTimeField(null=True, blank=True)

    # ── What the next caller inherits ──────────────────────────────────────
    #
    # FROZEN AT THE MOMENT OF HANDOVER, and that is the point rather than
    # duplication. The live disposition and remark travel with the lead, which
    # is right, but they are now the RECEIVING caller's editable fields: the
    # first thing Derek does is type over Bruce's sentence, and the context of
    # why he was given it disappears with it. These three keep it.
    #
    # The full history is still in CreditControlTouch, which is append-only and
    # loses nothing; this is the summary the next caller needs without reading
    # a log.
    handed_off_from = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="credit_control_handed_over",
    )
    handoff_disposition = models.CharField(max_length=100, blank=True, default="")
    handoff_remark = models.TextField(blank=True, default="")

    # ── The caller's work ──────────────────────────────────────────────────
    disposition = models.CharField(max_length=100, blank=True, default="")
    remark = models.TextField(blank=True, default="")
    next_action = models.CharField(max_length=255, blank=True, default="")
    callback_date = models.DateField(null=True, blank=True)
    last_touched_at = models.DateTimeField(null=True, blank=True)
    last_touched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="credit_control_touches",
    )

    # ── Outcome ────────────────────────────────────────────────────────────
    resolved = models.BooleanField(default=False)

    # ── Who gets the credit ────────────────────────────────────────────────
    #
    # STAMPED AT THE MOMENT IT RESOLVES, not derived later from assigned_to.
    # Those are different facts: a lead is still owned after it is paid, and a
    # later team change or reassignment would silently rewrite history. The
    # question "who collected this" has one correct answer and it is the one
    # that was true on the day.
    resolved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="credit_control_resolved",
    )
    # Had anybody actually worked it when it resolved? An invoice that was paid
    # before a caller ever reached the contact is a real and common outcome, and
    # counting it as somebody's win would flatter the whole team's numbers. The
    # dashboard reports the two separately for exactly that reason.
    resolved_after_effort = models.BooleanField(default=False)
    # The disposition it carried when it resolved, frozen. A caller can keep
    # editing a resolved lead's remark, and this is what the attribution report
    # was actually built on.
    resolved_disposition = models.CharField(max_length=100, blank=True, default="")
    # Why this lead is in Done, in words, derived from the LIVE source on each
    # refresh: "Paid 08 Sep", "Cancelled", "Removed from source". The Apps
    # Script build showed a frozen snapshot here, so a paid lead still read
    # "Pending" forever.
    done_reason = models.CharField(max_length=120, blank=True, default="")

    # ── Invoice type, the classifier's territory ───────────────────────────
    invoice_type = models.CharField(
        max_length=20, choices=InvoiceType.choices, blank=True, default="",
    )
    invoice_type_source = models.CharField(
        max_length=10, choices=TypeSource.choices, blank=True, default="",
    )
    # The human verdict. Always wins, and no automated path may write it.
    invoice_type_override = models.CharField(
        max_length=20, choices=InvoiceType.choices, blank=True, default="",
    )
    # A TextField and not a CharField(200). The classifier writes one short
    # sentence and slices it to 200 itself, but the verdicts lifted from the
    # Payment Collection sheet by `import_payment_sheet` run to 955 characters,
    # and a length cap here would have shredded 69 of the 117 justifications the
    # previous build had already reasoned out.
    invoice_type_basis = models.TextField(blank=True, default="")
    # The verbatim line from the evidence the verdict rests on. Checked against
    # the evidence before the verdict is stored, which is the cheap and
    # effective guard against a confident invention.
    invoice_type_quote = models.TextField(blank=True, default="")
    invoice_type_evidence_date = models.DateField(null=True, blank=True)
    invoice_type_confidence = models.FloatField(null=True, blank=True)
    invoice_type_model = models.CharField(max_length=50, blank=True, default="")
    invoice_type_prompt_version = models.CharField(max_length=20, blank=True, default="")
    invoice_type_attempts = models.PositiveIntegerField(default=0)
    invoice_type_error = models.CharField(max_length=300, blank=True, default="")
    invoice_type_at = models.DateTimeField(null=True, blank=True)

    # ── The handoff brief ──────────────────────────────────────────────────
    # Two or three lines of what happened so far, written when a lead changes
    # hands so the next caller opens with context instead of starting over.
    handoff_brief = models.TextField(blank=True, default="")
    handoff_brief_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "credit_control_leads"
        indexes = [
            models.Index(fields=["bucket", "assigned_to"]),
            models.Index(fields=["bucket", "first_seen"]),
            models.Index(fields=["callback_date"]),
        ]

    def __str__(self):
        return f"{self.invoice_id} -> {self.assigned_to or 'unassigned'}"

    @property
    def effective_invoice_type(self) -> str:
        """The human override if there is one, else what the classifier decided."""
        return self.invoice_type_override or self.invoice_type

    def days_pending(self, *, now=None) -> int | None:
        """
        Whole days since the invoice was raised.

        None when the invoice has no date, which is exactly the case that
        belongs in the Not Invoiced Yet bucket and must never be silently
        treated as zero days old.
        """
        invoice_date = getattr(self.invoice, "invoice_date", None)
        if not invoice_date:
            return None
        today = (now or timezone.now()).date()
        return (today - invoice_date).days


class CreditControlTouch(models.Model):
    """
    ONE ROW IS ONE CALL, by one user, to one lead.

    That is the definition and it does not bend. A caller rings a company, logs
    what happened as a disposition, and this table gains a row; so the count of
    rows for a given lead and a given user IS how many times that person has
    rung that invoice, which is what `my_calls` on the serializer reports and
    what the dashboard's per-shift matrix counts.

    NOT THE SAME NUMBER AS HubSpotContact.times_called, and the two are not
    rivals. That one is HubSpot's total against the contact, so it includes
    calls by people outside this team and calls placed before the invoice
    existed. This one is the CRM's own record of this team's attempts, so an
    absent row means no call was logged rather than meaning nobody has looked.

    Append-only. Never updated, never deleted by the application; `prune_logs`
    owns retention, like every other log table here.
    """
    lead = models.ForeignKey(
        CreditControlLead, on_delete=models.CASCADE, related_name="touches",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="credit_control_activity",
    )
    disposition = models.CharField(max_length=100, blank=True, default="")
    remark = models.TextField(blank=True, default="")
    # What changed, as a short field list, so the log reads without diffing.
    fields_changed = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "credit_control_touches"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["created_at", "user"])]

    def __str__(self):
        return f"{self.lead_id} by {self.user or 'system'} at {self.created_at:%Y-%m-%d %H:%M}"
