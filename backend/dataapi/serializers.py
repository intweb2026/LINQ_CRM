"""
dataapi/serializers.py
──────────────────────
Flat, read-only serializers. No nested writes, no hyperlinks — the consumer is
a spreadsheet, so every field has to land in one cell.

The delegate serializer exposes the resolved effective_* payment fields rather
than the raw delegate_* overrides, so the Sheets consumer does not have to
reimplement the COALESCE(delegate override, invoice value) rule that the CRM
applies at read time.
"""
from rest_framework import serializers

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from dataapi.models import DATA_API_SCOPES, DataApiKey
from events.models import Event
from ticket_central.models import Ticket


class DataApiBookingSerializer(serializers.ModelSerializer):
    sales_executive_name = serializers.SerializerMethodField()
    team_leader_name = serializers.SerializerMethodField()

    class Meta:
        model = BookEvent
        fields = [
            "id", "invoice_number", "event_code", "edition", "event_name", "event_date",
            "ticket_tier", "delegate_count",
            "discount", "discount_code",
            "pre_tax_amount", "tax_amount", "total_amount", "add_ons_total_amount",
            "currency",
            "company_name", "contact_name", "contact_email",
            "payment_status", "payment_type", "payment_date", "paid_or_free",
            "request_date", "invoice_date",
            "sales_executive", "sales_executive_name",
            "team_leader", "team_leader_name",
            "reference", "booking_code", "source",
            "created_at", "updated_at",
        ]

    def get_sales_executive_name(self, obj):
        if obj.sales_executive_id:
            u = obj.sales_executive
            return u.get_full_name() or u.username
        return None

    def get_team_leader_name(self, obj):
        if obj.team_leader_id:
            u = obj.team_leader
            return u.get_full_name() or u.username
        return None


class DataApiDelegateSerializer(serializers.ModelSerializer):
    # BookDelegate.invoice is a to_field FK on invoice_number, so the attname
    # invoice_id already holds the invoice-number string; no join needed.
    invoice_number = serializers.CharField(source="invoice_id")
    # Everything sourced from `invoice.` below is a real invoice column, so it
    # needs the join. DelegateDataViewSet select_related("invoice") already pays
    # for it once per page; see the assertNumQueries guard in tests_dataapi.py.
    #
    # These columns live on the INVOICE, not on book_delegates, but a booking
    # report row is one delegate and the invoice carries the half of it that is
    # shared. Pulling them through the FK here is what keeps the consumer from
    # having to join /delegates/ to /bookings/ in the spreadsheet itself.
    # RESOLVED, like every effective_* column below, and deliberately still
    # named request_date / invoice_date so the report's column order and
    # headings are untouched. A delegate may carry its own booking dates
    # (book_delegate/models.py delegate_request_date), and a feed that exported
    # the invoice's date would disagree with the CRM for exactly those rows.
    request_date = serializers.SerializerMethodField()
    invoice_date = serializers.SerializerMethodField()
    payment_due_date = serializers.DateField(source="invoice.payment_due_date", read_only=True)
    event_name = serializers.CharField(source="invoice.event_name", read_only=True)
    parent_code = serializers.CharField(source="invoice.parent_code", read_only=True)
    # The BILLING company on the invoice, which is not always the delegate's own
    # employer — an agency or a parent group books and is invoiced, while
    # company_display below stays whoever the delegate actually works for.
    account_company = serializers.CharField(source="invoice.company_name", read_only=True)
    # The id costs nothing (it is a column on the invoice row already fetched);
    # the name needs the user, which is why the viewset select_relates
    # invoice__sales_executive.
    sales_executive = serializers.IntegerField(source="invoice.sales_executive_id", read_only=True)
    sales_executive_name = serializers.SerializerMethodField()
    accounts_contact_email = serializers.SerializerMethodField()
    # The report has ONE Name column, so first_name and last_name are not
    # exposed here at all — a consumer that had both and the merge would have
    # three ways to spell the same person and no rule for which is canonical.
    # full_name is BookDelegate.full_name, the same merge every other read of a
    # delegate uses (book_delegate/models.py).
    full_name = serializers.ReadOnlyField()
    effective_payment_status = serializers.SerializerMethodField()
    effective_payment_type = serializers.SerializerMethodField()
    effective_payment_date = serializers.SerializerMethodField()
    effective_paid_or_free = serializers.SerializerMethodField()
    effective_ticket_tier = serializers.SerializerMethodField()
    company_display = serializers.SerializerMethodField()

    class Meta:
        model = BookDelegate
        # ORDER IS PART OF THE CONTRACT. DRF serialises in the order named
        # here, so this list IS the column order every fetch comes back in, and
        # a spreadsheet that writes the response straight into a sheet gets the
        # same headings in the same places on every run. The first 29 entries
        # are the booking report's own columns, in its own sequence; anything
        # added later belongs in the trailing block so the report's columns
        # never shift. tests_dataapi.py pins this order.
        #
        #                         ↓ the report's heading for each
        fields = [
            "effective_payment_status",   # Payment Status
            "event_code",                 # Event Code
            "booking_code",               # Booking Code
            "request_date",               # Request Date
            "invoice_date",               # Invoice Date
            "payment_due_date",           # Payment Due
            "invoice_number",             # Invoice Number
            "full_name",                  # Name
            "position",                   # Job Title
            "company_display",            # Delegate Company
            "email",                      # Delegate Email
            "phone_number",               # Direct Line
            "account_company",            # Account Company
            "accounts_contact_email",     # Accounts Contact
            "delegate_number",            # Delegate Number
            "effective_paid_or_free",     # Paid/Free
            "parent_code",                # Parent Code
            "effective_payment_date",     # Date Paid
            "effective_payment_type",     # Payment Type
            "effective_ticket_tier",      # Ticket Tier
            "discount",                   # Discount
            "add_ons",                    # Add-Ons
            "reference",                  # Ref
            "event_name",                 # Event Name
            "created_at",                 # Added Time
            "updated_at",                 # Modified Time
            "sales_executive_name",       # Sales Executive
            "id",                         # Record ID
            "attendance",                 # Attendance - IN?
            # ── Beyond the report ────────────────────────────────────────────
            # Not columns of the booking report, kept because consumers of this
            # endpoint predate it. They sit AFTER the 29 so adding or removing
            # one cannot move a report column.
            "edition", "delegate_count", "company_name_raw",
            "ticket_package", "sponsorship_level",
            "dietary_requirements", "notes",
            "sales_executive",            # the user id behind Sales Executive
        ]

    def get_effective_payment_status(self, obj):
        return obj.delegate_payment_status or (obj.invoice.payment_status if obj.invoice_id else "")

    def get_effective_payment_type(self, obj):
        return obj.delegate_payment_type or (obj.invoice.payment_type if obj.invoice_id else "")

    def get_effective_payment_date(self, obj):
        val = obj.delegate_payment_date or (obj.invoice.payment_date if obj.invoice_id else None)
        return str(val) if val else None

    def get_effective_paid_or_free(self, obj):
        return obj.delegate_paid_or_free or (obj.invoice.paid_or_free if obj.invoice_id else "")

    def get_request_date(self, obj):
        val = obj.delegate_request_date or obj.invoice.request_date
        return str(val) if val else None

    def get_invoice_date(self, obj):
        val = obj.delegate_invoice_date or obj.invoice.invoice_date
        return str(val) if val else None

    def get_effective_ticket_tier(self, obj):
        return obj.delegate_ticket_tier or (obj.invoice.ticket_tier if obj.invoice_id else "")

    def get_company_display(self, obj):
        if obj.company_id and obj.company:
            return obj.company.name
        return obj.company_name_raw

    def get_accounts_contact_email(self, obj):
        """
        Who the invoice is chased with, with the delegate's own address as the
        fallback — the same read-time rule book_delegate/serializers.py applies.

        book_delegate/accounts_contact.py fills the invoice column on write and
        has a backfill for the history, but rows written by paths that bypass
        save() (the Excel importer bulk_creates) can still be blank, and a blank
        billing contact in a report cell reads as "this booking has none" rather
        than "nobody filled the column in".
        """
        invoice = obj.invoice if obj.invoice_id else None
        raw = (getattr(invoice, "accounts_contact_email", "") or "").strip()
        return raw or (obj.email or "")

    def get_sales_executive_name(self, obj):
        invoice = obj.invoice if obj.invoice_id else None
        if invoice and invoice.sales_executive_id:
            u = invoice.sales_executive
            return u.get_full_name() or u.username
        return None


class DataApiEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        # NOTE: `edition` is deliberately absent. The master Event catalogue has
        # no edition column — edition is derived per booking from the trailing
        # year on event_code (see BookEvent.save) and lives on book_events and
        # book_delegates only.
        fields = [
            "id", "event_code", "name",
            "event_date", "end_date", "location", "venue",
            "status", "event_type",
            "web_bookings",
            "created_at", "updated_at",
        ]


class DataApiTicketSerializer(serializers.ModelSerializer):
    created_by = serializers.SerializerMethodField()
    mr_submitted_by = serializers.SerializerMethodField()
    dmd_submitted_by = serializers.SerializerMethodField()
    returned_by = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            "id", "ticket_number", "external_id", "event_code", "event_name", "status",
            "purpose", "link_url", "linkedin_keywords", "duplicate_tickets",
            "competitor_event_name", "organizer", "event_month_year", "event_location",
            "relationship", "type_of_ticket", "priority", "estimate", "mr_comments",
            "assigned_mr",
            "assign_name", "assign_date", "actual_number", "new_contacts_created",
            "source_spreadsheet_id", "source_tab", "source_row_number", "ticket_type",
            "complete_date", "hubspot_entry_date", "mined_count", "dm_comments",
            "assign_name_lx2", "actual_count_lx2", "complete_date_lx2", "dm_comments_lx2",
            "added_user_text", "created_by", "mr_submitted_by", "mr_submitted_at",
            "dmd_submitted_by", "dmd_submitted_at", "returned_by", "returned_at",
            "return_reason", "created_at", "updated_at",
        ]

    @staticmethod
    def _user_label(user):
        if user is None:
            return None
        full = f"{user.first_name} {user.last_name}".strip()
        return f"{full} ({user.username})" if full else user.username

    def get_created_by(self, obj):
        return self._user_label(obj.created_by)

    def get_mr_submitted_by(self, obj):
        return self._user_label(obj.mr_submitted_by)

    def get_dmd_submitted_by(self, obj):
        return self._user_label(obj.dmd_submitted_by)

    def get_returned_by(self, obj):
        return self._user_label(obj.returned_by)


# ── Key management (CRM admin UI, not the export surface) ───────────────────
# These two are read/written by session-authenticated admins through
# DataApiKeyManagementViewSet. They are NOT reachable with a dapi_ key.

class DataApiKeyListSerializer(serializers.ModelSerializer):
    created_by = serializers.StringRelatedField()
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = DataApiKey
        # key_hash is absent and must stay absent. key_preview is the only part
        # of the secret that exists after creation, and it is a truncation, so
        # it identifies a row without being replayable.
        fields = [
            "id", "name", "key_preview", "scopes", "is_active",
            "expires_at", "is_expired", "created_by", "created_at",
            "last_used_at", "usage_count", "rate_limit_per_minute",
        ]
        read_only_fields = fields

    def get_is_expired(self, obj):
        # An expired key is still is_active=True in the database; is_valid()
        # rejects it at auth time. The table would otherwise show it as active.
        return bool(obj.expires_at and not obj.is_valid() and obj.is_active)


class DataApiKeyCreateSerializer(serializers.Serializer):
    # 150 to match DataApiKey.name, so the form cannot accept a value the
    # column would then truncate or reject.
    name = serializers.CharField(max_length=150)
    # min_length=1: the model reads an empty scopes list as UNRESTRICTED, so
    # allowing an empty list here would turn "the admin picked nothing" into
    # "this key reads everything". A key with no scopes has to be a deliberate
    # act at the console, not the default outcome of an unfilled form.
    scopes = serializers.ListField(child=serializers.CharField(), min_length=1)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    VALID_SCOPES = set(DATA_API_SCOPES)

    def validate_scopes(self, value):
        invalid = set(value) - self.VALID_SCOPES
        if invalid:
            raise serializers.ValidationError(
                f"Invalid scopes: {', '.join(sorted(invalid))}. "
                f"Valid scopes: {', '.join(sorted(self.VALID_SCOPES))}."
            )
        return sorted(set(value))
