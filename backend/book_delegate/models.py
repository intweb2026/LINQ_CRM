"""
book_delegate/models.py
────────────────────────
Individual attendee linked to an invoice.

- payment_status is READ from invoice (@property) — never stored here.
- company FK enables CRM contact reuse across events.
- first_name/last_name stored split for mail-merge use.
- Unique: (invoice, email) — silently skipped on bulk import.
"""
from django.db import models
from django.utils import timezone


class BookDelegate(models.Model):
    class Attendance(models.TextChoices):
        PENDING   = "Pending",   "Pending"
        CONFIRMED = "Confirmed", "Confirmed"
        NO_SHOW   = "No-show",   "No-show"
        CANCELLED = "Cancelled", "Cancelled"

    invoice = models.ForeignKey(
        "book_event.BookEvent",
        on_delete=models.CASCADE,
        related_name="delegates",
        to_field="invoice_number",
        db_column="invoice_number",
        db_constraint=False,
    )
    event_code       = models.CharField(max_length=50, db_index=True)
    edition          = models.IntegerField(null=True, blank=True, db_index=True)
    # See events/models.py for the rationale — one value per load_zoho_export run.
    import_batch_id  = models.UUIDField(null=True, blank=True, db_index=True)
    company          = models.ForeignKey(
        "companies.Company", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="delegates",
    )
    company_name_raw = models.CharField(max_length=255, blank=True, default="")
    first_name       = models.CharField(max_length=150)
    last_name        = models.CharField(max_length=150, blank=True, default="")
    email            = models.EmailField(db_index=True)
    phone_number     = models.CharField(max_length=50, blank=True, default="")
    position          = models.CharField(max_length=150, blank=True, default="")
    ticket_package    = models.CharField(max_length=100, blank=True, default="")
    sponsorship_level = models.CharField(max_length=100, blank=True, default="")
    attendance        = models.CharField(
        max_length=20, choices=Attendance.choices,
        default=Attendance.PENDING, db_index=True,
    )
    delegate_number = models.IntegerField(default=1)
    delegate_count = models.IntegerField(default=1, choices=[(0, "0"), (1, "1")])  # Strictly 0 or 1
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    add_ons = models.TextField(blank=True, default="")
    reference = models.CharField(max_length=255, blank=True, default="")
    dietary_requirements = models.CharField(max_length=255, blank=True, default="")
    notes            = models.TextField(blank=True, default="")

    def save(self, *args, **kwargs):
        # Force delegate_count to 0 if payment status is Cancelled
        if self.delegate_payment_status == "Cancelled":
            self.delegate_count = 0
        elif self.pk:
            # Restore on the TRANSITION off Cancelled only. A blanket
            # "if delegate_count == 0: set 1" would clobber a deliberate zero —
            # the field declares choices=[(0,"0"),(1,"1")] and is writable in
            # the serializer, so 0 on a non-cancelled delegate is legitimate.
            prev = (
                BookDelegate.objects
                .filter(pk=self.pk)
                .values_list("delegate_payment_status", flat=True)
                .first()
            )
            if prev == "Cancelled":
                self.delegate_count = 1
        if self.event_code:
            import re
            match = re.search(r'(\d{2,4})$', self.event_code)
            if match:
                num_str = match.group(1)
                self.edition = int(f"20{num_str}" if len(num_str) == 2 else num_str)
                self.event_code = re.sub(r'\s*-?\s*\d{2,4}$', '', self.event_code).strip()
        elif self.invoice:
            self.event_code = self.invoice.event_code
            self.edition = self.invoice.edition
        super().save(*args, **kwargs)

    # Per-delegate payment overrides (null = inherit from invoice)
    delegate_payment_status = models.CharField(max_length=50, blank=True, null=True, default=None)
    delegate_payment_type   = models.CharField(max_length=50, blank=True, null=True, default=None)
    delegate_payment_date   = models.DateField(blank=True, null=True, default=None)
    delegate_paid_or_free   = models.CharField(max_length=20, blank=True, null=True, default=None)
    delegate_ticket_tier    = models.CharField(max_length=50, blank=True, null=True, default=None)
    created_at       = models.DateTimeField(default=timezone.now)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        db_table       = "book_delegates"
        ordering       = ["invoice__invoice_number", "first_name"]
        unique_together = [("invoice", "email")]
        indexes = [
            models.Index(fields=["invoice", "email"]),
            models.Index(fields=["event_code"]),
            models.Index(fields=["edition"]),
            models.Index(fields=["email"]),
            models.Index(fields=["company"]),
            models.Index(fields=["attendance"]),
        ]

    def __str__(self):
        return f"{self.full_name} <{self.email}>"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def payment_status(self):
        return self.invoice.payment_status

    @property
    def payment_date(self):
        return self.invoice.payment_date

    @property
    def invoice_number(self):
        return self.invoice_id

    @property
    def company_display(self):
        return self.company.name if self.company_id else self.company_name_raw
