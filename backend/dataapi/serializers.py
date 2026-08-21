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
    effective_payment_status = serializers.SerializerMethodField()
    effective_payment_type = serializers.SerializerMethodField()
    effective_payment_date = serializers.SerializerMethodField()
    effective_paid_or_free = serializers.SerializerMethodField()
    effective_ticket_tier = serializers.SerializerMethodField()
    company_display = serializers.SerializerMethodField()

    class Meta:
        model = BookDelegate
        fields = [
            "id", "invoice_number", "event_code", "edition",
            "first_name", "last_name", "email", "phone_number", "position",
            "company_name_raw", "company_display",
            "ticket_package", "sponsorship_level", "attendance",
            "booking_code", "delegate_number", "delegate_count",
            "discount", "add_ons", "reference",
            "dietary_requirements", "notes",
            "effective_payment_status", "effective_payment_type",
            "effective_payment_date", "effective_paid_or_free",
            "effective_ticket_tier",
            "created_at", "updated_at",
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

    def get_effective_ticket_tier(self, obj):
        return obj.delegate_ticket_tier or (obj.invoice.ticket_tier if obj.invoice_id else "")

    def get_company_display(self, obj):
        if obj.company_id and obj.company:
            return obj.company.name
        return obj.company_name_raw


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
