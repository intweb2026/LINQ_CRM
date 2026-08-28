"""
book_delegate/models.py
────────────────────────
Individual attendee linked to an invoice.

- payment_status is READ from invoice (@property) — never stored here.
- company FK enables CRM contact reuse across events.
- first_name/last_name stored split for mail-merge use.
- Unique: (invoice, email, first_name, last_name) — one PERSON per invoice.
  Several delegates may share one email address; see Meta.constraints.
"""
from django.db import models
from django.utils import timezone

from book_event.booking_code_canonical import canonicalize_on_save

from .accounts_contact import fill_accounts_contact_from_delegate


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
    # Per-delegate booking code. It lives HERE as well as on the invoice because
    # one invoice can carry delegates booked on different terms — a Speaker and a
    # Group Pass on the same invoice is a real combination — and BookEvent has
    # exactly one booking_code to describe all of them. Populated for every
    # existing row by migration 0009, and defaulted from the invoice in save()
    # below so a row created without one is never left blank.
    #
    # The invoice column is NOT retired: revenue classification reads
    # invoice__booking_code (book_event/views.py:195, config/views.py:244) and
    # sync/bookings_sync.py exports it. The Bookings modal keeps it in step by
    # writing the delegates' shared code back to the invoice whenever every
    # delegate on it agrees — see frontend/src/api/bookings.js.
    booking_code    = models.CharField(max_length=100, blank=True, default="", db_index=True)
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
        # Inherit the invoice's code when this delegate has none of its own, the
        # same way event_code is inherited above. Without it, rows created by the
        # website intake — which sets booking_code on the invoice only — would read
        # as blank in the Bookings table now that the column is delegate-sourced.
        if not self.booking_code and self.invoice_id:
            self.booking_code = self.invoice.booking_code
        # Canonical spelling, applied AFTER the inheritance above so a code
        # inherited from an invoice is canonicalised too. Same chokepoint
        # reasoning as BookEvent.save(); see booking_code_canonical.py.
        args, kwargs = canonicalize_on_save(self, args, kwargs)
        # Last derivation before the write, so it sees the final invoice_id.
        self.booked_on = self._derive_booked_on()
        super().save(*args, **kwargs)
        # AFTER the write, and only ever into a BLANK column: an invoice with no
        # accounts contact of its own takes this delegate's email, so a booking
        # entered from now on is never left with nobody to bill. Anything already
        # stored there — including an address a sales exec typed by hand — is left
        # exactly as it is. See accounts_contact.py; the existing history is
        # filled by `manage.py backfill_accounts_contact_email`.
        fill_accounts_contact_from_delegate(self)

    def _derive_booked_on(self):
        """
        COALESCE(delegate_request_date, invoice.request_date,
                 delegate_invoice_date, invoice.invoice_date), without a query
        in the paths that matter.

        The two delegate columns lead because they are this delegate's own
        booking date where one is set; the flattened COALESCE is the same thing
        as COALESCE(effective_request_date, effective_invoice_date) written out,
        so a delegate that overrides only the invoice date still inherits the
        invoice's request date ahead of it.

        The bookings list queryset and the mass-update engine both select_related
        the invoice, so the related object is already in _state.fields_cache and
        this costs nothing on every write those paths perform. The fallback is a
        two-column values_list rather than touching self.invoice, because that
        descriptor would fetch every column of the invoice row to read two dates.

        NOTE ON self.invoice_id. BookDelegate.invoice is a ForeignKey declared
        with to_field="invoice_number" and db_column="invoice_number", so the
        attname invoice_id holds an invoice-number STRING, not an integer pk.
        That is why the fallback filters on invoice_number= and not on pk=, and
        why the empty-string check below sits alongside the None check: a blank
        varchar is the "unset" value for this column in a way it never is for an
        integer FK.
        """
        # The delegate's own request date settles it without reading the
        # invoice at all; the rest of the chain only decides what an
        # un-overridden row inherits.
        if self.delegate_request_date:
            return self.delegate_request_date
        if self.invoice_id is None or self.invoice_id == "":
            return self.delegate_invoice_date
        cached = self._state.fields_cache.get("invoice")
        if cached is not None:
            return (cached.request_date
                    or self.delegate_invoice_date
                    or cached.invoice_date)
        from book_event.models import BookEvent
        row = (BookEvent.objects
               .filter(invoice_number=self.invoice_id)
               .values_list("request_date", "invoice_date")
               .first())
        if not row:
            return self.delegate_invoice_date
        return row[0] or self.delegate_invoice_date or row[1]

    # Per-delegate payment overrides (null = inherit from invoice)
    delegate_payment_status = models.CharField(max_length=50, blank=True, null=True, default=None)
    delegate_payment_type   = models.CharField(max_length=50, blank=True, null=True, default=None)
    delegate_payment_date   = models.DateField(blank=True, null=True, default=None)
    delegate_paid_or_free   = models.CharField(max_length=20, blank=True, null=True, default=None)
    delegate_ticket_tier    = models.CharField(max_length=50, blank=True, null=True, default=None)
    # Per-delegate BOOKING DATE overrides, same rule; null inherits the invoice.
    #
    # WHY THESE EXIST. Request Date and Invoice Date were invoice columns and
    # nothing else, so the two cells the booking modal shows on every delegate
    # row were one shared value; setting one delegate's request date set it for
    # everybody on the invoice. Delegates on a single invoice are routinely
    # booked on the same day, which is why one column was enough for so long,
    # but they are not required to be, and a correction to one person's row must
    # not move the others. That is the same fact the five overrides above
    # record, and it is recorded the same way.
    #
    # The INVOICE columns are NOT retired, for the reason the booking_code note
    # above gives: BookEvent.request_date is what the invoice-level reads use,
    # including the dashboards' period window and sync/bookings_sync.py. The
    # Bookings modal keeps them in step by writing the delegates' shared date
    # back to the invoice whenever every delegate on it agrees, which is the
    # normal case; see frontend/src/api/bookings.js splitPersonLevel.
    delegate_request_date   = models.DateField(blank=True, null=True, default=None)
    delegate_invoice_date   = models.DateField(blank=True, null=True, default=None)

    # Denormalised booking date, COALESCE(invoice.request_date, invoice.invoice_date).
    #
    # WHY A COLUMN AND NOT THE EXISTING ANNOTATION
    # The default ordering was -_sort_request_date, an F() on invoice__request_date,
    # with StableOrderingFilter appending book_delegates.id. That is ORDER BY on the
    # JOINED table and a tiebreak on the DRIVING table, and the join itself is on a
    # varchar invoice_number rather than an integer pk, so every page fetch and every
    # background poll sorted the whole joined set to return 50 rows. Measured before
    # this change, the plan read:
    #     Sort Key: book_events.request_date DESC, book_delegates.id
    #     Hash Cond: (book_delegates.invoice_number)::text = (book_events.invoice_number)::text
    # Held here, the same order is one index scan on (booked_on DESC, id DESC).
    #
    # WHY COALESCE AND NOT request_date ALONE
    # accounts/period_filter.py already windows this resource on
    # COALESCE(request_date, invoice_date). The sort and the window now agree by
    # construction rather than by coincidence; previously a delegate with no
    # request_date was inside the window but sorted as NULL.
    #
    # DERIVED, NEVER AUTHORED. editable=False keeps it out of every ModelForm and
    # every serializer that builds fields from the model. save() below and
    # BookEvent.save() are the only writers, plus the one-time backfill in
    # backend/sql/2026_08_booked_on.sql.
    booked_on = models.DateField(null=True, blank=True, editable=False)

    created_at       = models.DateTimeField(default=timezone.now)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        db_table       = "book_delegates"
        ordering       = ["invoice__invoice_number", "first_name"]
        # ONE PERSON per invoice, not one EMAIL per invoice.
        #
        # This was unique_together = [("invoice", "email")], which is not what a
        # booking is. One email address routinely covers several delegates on one
        # invoice: a ranch office address booking two owners, a PA booking their
        # whole team, an info@ address on a group pass. Under the old key the
        # second person could not be stored at all, so the website intake
        # OVERWROTE the first with the second and reported success, and the Excel
        # importer invented a `dup-xxxxxxxx@import.local` placeholder to get the
        # row in at the cost of the real address.
        #
        # The name is what separates two people sharing an address, so it belongs
        # in the key. What the constraint still refuses is the same person twice
        # on one invoice, which is the duplicate that was ever worth refusing.
        #
        # Named explicitly rather than left to unique_together's generated name,
        # because the migration that replaces the old key has to find it by name
        # in a database where the old one may never have been created — see
        # migration 0017.
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "email", "first_name", "last_name"],
                name="book_delegates_invoice_person_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["invoice", "email"]),
            models.Index(fields=["event_code"]),
            models.Index(fields=["edition"]),
            models.Index(fields=["email"]),
            models.Index(fields=["company"]),
            models.Index(fields=["attendance"]),
            # The default Bookings sort, now single-table. Spelled as expressions
            # with nulls_last rather than fields=["-booked_on", "-id"], because
            # that form emits DESC NULLS FIRST and would park every delegate whose
            # invoice carries no date at the TOP of the table. Same reasoning, and
            # the same spelling, as book_events_reqdate_id_idx.
            models.Index(
                models.F("booked_on").desc(nulls_last=True),
                models.F("id").desc(),
                name="book_delegates_booked_id_idx",
            ),
            # The CURRENT default Bookings sort, ["-created_at", "-id"] — see the
            # long note on BookDelegateViewSet.ordering for why it moved off
            # booked_on. Spelled DESC/DESC so the index matches that ORDER BY
            # exactly and the page is one index scan; fields=["-created_at", "-id"]
            # would express the same thing, but the expression form is used here to
            # stay consistent with the booked_on index directly above.
            #
            # created_at is NOT NULL, so no nulls_last clause is needed or wanted.
            models.Index(
                models.F("created_at").desc(),
                models.F("id").desc(),
                name="book_delegates_created_id_idx",
            ),
            # The CURRENT default Bookings sort, ["-updated_at", "-id"] — newest
            # MODIFIED first; see the note on BookDelegateViewSet.ordering. Without
            # this, every page of ~14,800 delegates is a full sort rather than one
            # index scan, because the created_at index above cannot serve an
            # updated_at ORDER BY.
            #
            # updated_at is auto_now=True and therefore NOT NULL, so no nulls_last
            # clause is needed or wanted. Spelled DESC/DESC to match the ORDER BY
            # exactly, in the expression form the two indexes above use.
            models.Index(
                models.F("updated_at").desc(),
                models.F("id").desc(),
                name="book_delegates_updated_id_idx",
            ),
        ]

    def __str__(self):
        return f"{self.full_name} <{self.email}>"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @staticmethod
    def person_key(email, first_name, last_name):
        """
        Who a delegate IS, within one invoice; the Meta.constraints key in the
        form code can compare.

        Lives here rather than in each caller because it is the same question the
        database constraint answers, and two spellings of it would drift. The
        webhook ingest path matches incoming delegates with it, and the transfer
        endpoint tests a destination invoice with it.

        Case- and whitespace-insensitive: the same person arriving as "Emily" on
        one delivery and " emily " on the next is one person.
        """
        return (
            (email or "").strip().lower(),
            (first_name or "").strip().lower(),
            (last_name or "").strip().lower(),
        )

    @property
    def own_person_key(self):
        return self.person_key(self.email, self.first_name, self.last_name)

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
