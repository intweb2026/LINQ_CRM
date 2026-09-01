from rest_framework import serializers
from .models import BookDelegate
from companies.serializers import CompanyMiniSerializer


class BookDelegateInlineSerializer(serializers.ModelSerializer):
    full_name                  = serializers.ReadOnlyField()
    payment_status             = serializers.CharField(source="invoice.payment_status", read_only=True)
    company_display            = serializers.ReadOnlyField()
    effective_payment_status   = serializers.SerializerMethodField()
    effective_payment_type     = serializers.SerializerMethodField()
    effective_payment_date     = serializers.SerializerMethodField()
    effective_paid_or_free     = serializers.SerializerMethodField()
    effective_ticket_tier      = serializers.SerializerMethodField()
    effective_request_date     = serializers.SerializerMethodField()
    effective_invoice_date     = serializers.SerializerMethodField()

    class Meta:
        model  = BookDelegate
        fields = [
            "id", "first_name", "last_name", "full_name",
            "email", "phone_number", "position",
            "ticket_package", "sponsorship_level",
            "company_display", "attendance", "payment_status",
            "dietary_requirements", "notes",
            "delegate_payment_status", "delegate_payment_type", "delegate_payment_date",
            "delegate_paid_or_free", "delegate_ticket_tier",
            "delegate_request_date", "delegate_invoice_date",
            "booking_code", "delegate_number",
            "delegate_count", "discount", "add_ons", "reference",
            "effective_payment_status", "effective_payment_type", "effective_payment_date",
            "effective_paid_or_free", "effective_ticket_tier",
            "effective_request_date", "effective_invoice_date", "edition",
        ]

    def get_effective_payment_status(self, obj):
        return obj.delegate_payment_status or obj.invoice.payment_status

    def get_effective_payment_type(self, obj):
        return obj.delegate_payment_type or obj.invoice.payment_type

    def get_effective_payment_date(self, obj):
        val = obj.delegate_payment_date or obj.invoice.payment_date
        return str(val) if val else None

    def get_effective_paid_or_free(self, obj):
        return obj.delegate_paid_or_free or obj.invoice.paid_or_free

    def get_effective_ticket_tier(self, obj):
        return obj.delegate_ticket_tier or obj.invoice.ticket_tier

    def get_effective_request_date(self, obj):
        val = obj.delegate_request_date or obj.invoice.request_date
        return str(val) if val else None

    def get_effective_invoice_date(self, obj):
        val = obj.delegate_invoice_date or obj.invoice.invoice_date
        return str(val) if val else None


class BookDelegateListSerializer(serializers.ModelSerializer):
    full_name              = serializers.ReadOnlyField()
    payment_status         = serializers.CharField(source="invoice.payment_status",  read_only=True)
    payment_date           = serializers.DateField(source="invoice.payment_date",    read_only=True)
    invoice_number         = serializers.CharField(source="invoice.invoice_number",  read_only=True)
    book_event_id          = serializers.IntegerField(source="invoice.id",           read_only=True)
    # The INVOICE's own two dates, raw, exactly as payment_status above is the
    # invoice's raw status. What the Bookings table shows is the RESOLVED pair,
    # effective_request_date and effective_invoice_date below, because a delegate
    # may carry its own date now (models.py delegate_request_date). Both are
    # reported, so the booking modal can edit the override while anything that
    # means the invoice's shared date still has it.
    request_date           = serializers.DateField(source="invoice.request_date",    read_only=True)
    invoice_date           = serializers.DateField(source="invoice.invoice_date",    read_only=True)
    # The DELEGATE's own column now (models.py), not invoice.booking_code: one
    # invoice can carry delegates on different booking codes, and the previous
    # source= meant every row on an invoice reported the same value no matter what
    # was stored against the delegate. Left writable so the Bookings modal can
    # save it — it was read_only here, which is why editing it did nothing.
    currency               = serializers.CharField(source="invoice.currency",        read_only=True)
    discount               = serializers.DecimalField(max_digits=10, decimal_places=2)
    discount_code          = serializers.CharField(source="invoice.discount_code",   read_only=True)
    pre_tax_amount         = serializers.DecimalField(source="invoice.pre_tax_amount", max_digits=12, decimal_places=2, read_only=True, allow_null=True)
    tax_amount             = serializers.DecimalField(source="invoice.tax_amount",   max_digits=12, decimal_places=2, read_only=True, allow_null=True)
    total_amount           = serializers.DecimalField(source="invoice.total_amount", max_digits=12, decimal_places=2, read_only=True, allow_null=True)
    add_ons_total_amount   = serializers.DecimalField(source="invoice.add_ons_total_amount", max_digits=12, decimal_places=2, read_only=True, allow_null=True)
    delegate_count         = serializers.IntegerField()
    paid_free              = serializers.CharField(source="invoice.paid_free",       read_only=True)
    add_ons                = serializers.CharField()
    reference              = serializers.CharField()
    event_name             = serializers.CharField(source="invoice.event_name",      read_only=True)
    ticket_tier            = serializers.CharField(source="invoice.ticket_tier",     read_only=True)
    source                 = serializers.CharField(source="invoice.source",          read_only=True)
    # RESOLVED, not raw: the invoice's own accounts contact, falling back to this
    # delegate's own email where the invoice has none. Accounts Contact is who the
    # invoice is chased with, and a blank one is not a different person — it is a
    # gap, and the delegate is the only address anybody had for that booking. The
    # fallback is computed on READ so it always follows the delegate's current
    # email; nothing is written, and `accounts_contact_email_raw` below still
    # reports what is actually stored so the booking modal can edit the real
    # column rather than saving its own fallback back over it.
    accounts_contact_email     = serializers.SerializerMethodField()
    accounts_contact_email_raw = serializers.EmailField(source="invoice.accounts_contact_email", read_only=True)
    sales_executive_name   = serializers.SerializerMethodField()
    team_leader_name       = serializers.SerializerMethodField()
    paid_or_free           = serializers.CharField(source="invoice.paid_or_free",       read_only=True)
    company_display        = serializers.ReadOnlyField()
    effective_payment_status = serializers.SerializerMethodField()
    effective_payment_type   = serializers.SerializerMethodField()
    effective_payment_date   = serializers.SerializerMethodField()
    effective_paid_or_free   = serializers.SerializerMethodField()
    effective_ticket_tier    = serializers.SerializerMethodField()
    effective_request_date   = serializers.SerializerMethodField()
    effective_invoice_date   = serializers.SerializerMethodField()

    class Meta:
        model  = BookDelegate
        fields = [
            "id", "book_event_id", "invoice_number", "event_code", "edition", "booking_code",
            "request_date", "invoice_date", "first_name", "last_name", "full_name",
            "email", "phone_number", "position",
            "ticket_package", "sponsorship_level",
            "delegate_number", "company_display",
            "attendance", "payment_status", "payment_date",
            "currency", "discount", "discount_code",
            "pre_tax_amount", "tax_amount", "total_amount", "add_ons_total_amount",
            "delegate_count", "ticket_tier", "paid_or_free",
            "sales_executive_name", "team_leader_name",
            "paid_free", "add_ons", "reference",
            "event_name", "accounts_contact_email", "accounts_contact_email_raw", "source",
            "delegate_payment_status", "delegate_payment_type", "delegate_payment_date",
            "delegate_paid_or_free", "delegate_ticket_tier",
            "delegate_request_date", "delegate_invoice_date",
            "effective_payment_status", "effective_payment_type", "effective_payment_date",
            "effective_paid_or_free", "effective_ticket_tier",
            "effective_request_date", "effective_invoice_date",
            "created_at", "updated_at",
        ]

    def get_accounts_contact_email(self, obj):
        return (obj.invoice.accounts_contact_email or "").strip() or (obj.email or "")

    def get_sales_executive_name(self, obj):
        if obj.invoice.sales_executive_id:
            u = obj.invoice.sales_executive
            return u.get_full_name() or u.username
        return None

    def get_team_leader_name(self, obj):
        if obj.invoice.team_leader_id:
            u = obj.invoice.team_leader
            return u.get_full_name() or u.username
        return None

    def get_effective_payment_status(self, obj):
        return obj.delegate_payment_status or obj.invoice.payment_status

    def get_effective_payment_type(self, obj):
        return obj.delegate_payment_type or obj.invoice.payment_type

    def get_effective_payment_date(self, obj):
        val = obj.delegate_payment_date or obj.invoice.payment_date
        return str(val) if val else None

    def get_effective_paid_or_free(self, obj):
        return obj.delegate_paid_or_free or obj.invoice.paid_or_free

    def get_effective_ticket_tier(self, obj):
        return obj.delegate_ticket_tier or obj.invoice.ticket_tier

    def get_effective_request_date(self, obj):
        val = obj.delegate_request_date or obj.invoice.request_date
        return str(val) if val else None

    def get_effective_invoice_date(self, obj):
        val = obj.delegate_invoice_date or obj.invoice.invoice_date
        return str(val) if val else None


class BookDelegateDetailSerializer(serializers.ModelSerializer):
    full_name       = serializers.ReadOnlyField()
    payment_status  = serializers.CharField(source="invoice.payment_status", read_only=True)
    payment_date    = serializers.DateField(source="invoice.payment_date",   read_only=True)
    invoice_number  = serializers.CharField(source="invoice.invoice_number", read_only=True)
    company_display = serializers.ReadOnlyField()
    company_detail  = CompanyMiniSerializer(source="company", read_only=True)
    event_name      = serializers.SerializerMethodField()
    paid_or_free    = serializers.CharField(source="invoice.paid_or_free", read_only=True)
    effective_payment_status = serializers.SerializerMethodField()
    effective_payment_type   = serializers.SerializerMethodField()
    effective_payment_date   = serializers.SerializerMethodField()
    effective_paid_or_free   = serializers.SerializerMethodField()
    effective_ticket_tier    = serializers.SerializerMethodField()
    effective_request_date   = serializers.SerializerMethodField()
    effective_invoice_date   = serializers.SerializerMethodField()

    class Meta:
        model  = BookDelegate
        fields = [
            "id", "invoice_number", "event_code", "edition", "event_name",
            "first_name", "last_name", "full_name",
            "email", "phone_number", "position",
            "ticket_package", "sponsorship_level",
            "company", "company_detail", "company_name_raw", "company_display",
            "attendance", "payment_status", "payment_date", "paid_or_free",
            "dietary_requirements", "notes",
            "delegate_payment_status", "delegate_payment_type", "delegate_payment_date",
            "delegate_paid_or_free", "delegate_ticket_tier",
            "delegate_request_date", "delegate_invoice_date",
            "delegate_count", "discount", "add_ons", "reference",
            "effective_payment_status", "effective_payment_type", "effective_payment_date",
            "effective_paid_or_free", "effective_ticket_tier",
            "effective_request_date", "effective_invoice_date",
            "created_at", "updated_at",
        ]

    def get_event_name(self, obj):
        if obj.invoice and obj.invoice.event_name:
            return obj.invoice.event_name
        from events.models import Event
        try:
            import re
            master_event = Event.objects.get(event_code=obj.event_code)
            clean_name = re.sub(r'\s*\d{4}$', '', master_event.name).strip()
            if obj.edition:
                return f"{clean_name} {obj.edition}"
            return clean_name
        except Event.DoesNotExist:
            return ""

    def get_effective_payment_status(self, obj):
        return obj.delegate_payment_status or obj.invoice.payment_status

    def get_effective_payment_type(self, obj):
        return obj.delegate_payment_type or obj.invoice.payment_type

    def get_effective_payment_date(self, obj):
        val = obj.delegate_payment_date or obj.invoice.payment_date
        return str(val) if val else None

    def get_effective_paid_or_free(self, obj):
        return obj.delegate_paid_or_free or obj.invoice.paid_or_free

    def get_effective_ticket_tier(self, obj):
        return obj.delegate_ticket_tier or obj.invoice.ticket_tier

    def get_effective_request_date(self, obj):
        val = obj.delegate_request_date or obj.invoice.request_date
        return str(val) if val else None

    def get_effective_invoice_date(self, obj):
        val = obj.delegate_invoice_date or obj.invoice.invoice_date
        return str(val) if val else None


class BookDelegateWriteSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(write_only=True)

    class Meta:
        model  = BookDelegate
        fields = [
            "invoice_number", "event_code",
            "first_name", "last_name", "email", "phone_number", "position",
            "ticket_package", "sponsorship_level",
            "company", "attendance", "dietary_requirements", "notes",
            "delegate_payment_status", "delegate_payment_type", "delegate_payment_date",
            "delegate_paid_or_free", "delegate_ticket_tier",
            "delegate_request_date", "delegate_invoice_date",
            "booking_code", "delegate_number",
            "delegate_count", "discount", "add_ons", "reference",
        ]

    def validate_invoice_number(self, value):
        from book_event.models import BookEvent
        try:
            self._invoice = BookEvent.objects.get(invoice_number=value)
        except BookEvent.DoesNotExist:
            raise serializers.ValidationError(f"Invoice '{value}' not found.")
        return value

    def create(self, validated_data):
        invoice_number = validated_data.pop("invoice_number")
        invoice = getattr(self, "_invoice", None)
        if not invoice:
            from book_event.models import BookEvent
            invoice = BookEvent.objects.get(invoice_number=invoice_number)
        validated_data["invoice"]    = invoice
        validated_data["event_code"] = validated_data.get("event_code") or invoice.event_code
        return BookDelegate.objects.create(**validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("invoice_number", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance
