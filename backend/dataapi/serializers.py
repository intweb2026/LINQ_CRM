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
from events.models import Event


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
