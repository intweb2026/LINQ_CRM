"""
book_event/serializers.py
"""
from decimal import Decimal, InvalidOperation
from rest_framework import serializers
from .models import BookEvent


# Fields visible only to admin users
_ADMIN_ONLY_FIELDS = frozenset([
    "discount", "discount_code",
    "pre_tax_amount", "tax_amount", "total_amount", "add_ons_total_amount",
    "sales_executive", "team_leader",
])

# Company write-only fields passed from the form, handled by create/update
_COMPANY_WRITE_FIELDS = (
    "company_address", "company_city", "company_state",
    "company_country", "company_postal_code", "company_website",
)

# What the nested `delegates` payload is allowed to write.
#
# DECLARED ONCE. create() and update() each held their own copy, and a key
# missing from one of them is invisible from the outside: the request still
# answers 200/201, the response still echoes a delegate, and only the value the
# user typed is gone. booking_code and delegate_number were already lost that
# way once — see the suite in tests_booking_modal_writes.py.
#
# company_name_raw is here because Delegate Company is a REQUIRED column in the
# Bookings modals. It was in neither copy, so the value was filtered out here
# after surviving the trip: every hand-entered booking stored a blank company,
# which is the field the Bookings tab displays (BookDelegate.company_display)
# and searches on (book_delegate/views.py search_fields).
_ALLOWED_DELEGATE = frozenset({
    "first_name", "last_name", "email", "phone_number",
    "company_name_raw",
    "position", "ticket_package", "sponsorship_level",
    "attendance", "notes", "dietary_requirements",
    "delegate_payment_status", "delegate_payment_type", "delegate_payment_date",
    "delegate_paid_or_free", "delegate_ticket_tier",
    "booking_code", "delegate_number",
    "delegate_count", "discount", "add_ons", "reference",
})


class BookEventListSerializer(serializers.ModelSerializer):
    sales_executive_name  = serializers.SerializerMethodField()
    team_leader_name      = serializers.SerializerMethodField()
    delegate_count_actual = serializers.SerializerMethodField()
    updated_by_name       = serializers.SerializerMethodField()

    class Meta:
        model  = BookEvent
        fields = [
            "id", "invoice_number", "event_code", "edition", "event_name", "event_date",
            "ticket_tier", "delegate_count", "delegate_count_actual",
            "discount", "discount_code",
            "pre_tax_amount", "tax_amount", "total_amount", "add_ons_total_amount",
            "currency",
            "company_name", "contact_name", "contact_email",
            "payment_status", "payment_type", "payment_date", "paid_or_free",
            "ticket_tier", "request_date", "invoice_date",
            "sales_executive", "sales_executive_name",
            "team_leader", "team_leader_name",
            "reference", "booking_code", "source", "created_at", "updated_at",
            "updated_by", "updated_by_name",
        ]
        read_only_fields = ["id", "invoice_number", "created_at", "updated_at"]

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        request = self.context.get("request")
        if request and not getattr(request.user, "is_admin", False):
            for f in _ADMIN_ONLY_FIELDS:
                ret.pop(f, None)
        return ret

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

    def get_delegate_count_actual(self, obj):
        return getattr(obj, "_delegate_count_actual", None)

    def get_updated_by_name(self, obj):
        if obj.updated_by_id:
            u = obj.updated_by
            return u.get_full_name() or u.username
        return None


class BookEventDetailSerializer(serializers.ModelSerializer):
    delegates            = serializers.JSONField(required=False)
    sales_executive_name = serializers.SerializerMethodField()
    team_leader_name     = serializers.SerializerMethodField()
    event_detail         = serializers.SerializerMethodField()
    updated_by_name      = serializers.SerializerMethodField()

    # Write-only company address fields — stripped before DB save, used for Company upsert
    company_address    = serializers.CharField(write_only=True, required=False, default="", allow_blank=True)
    company_city       = serializers.CharField(write_only=True, required=False, default="", allow_blank=True)
    company_state      = serializers.CharField(write_only=True, required=False, default="", allow_blank=True)
    company_country    = serializers.CharField(write_only=True, required=False, default="", allow_blank=True)
    company_postal_code = serializers.CharField(write_only=True, required=False, default="", allow_blank=True)
    company_website    = serializers.CharField(write_only=True, required=False, default="", allow_blank=True)

    class Meta:
        model  = BookEvent
        fields = [
            "id", "invoice_number", "event_code", "edition", "event_name", "event_date",
            "ticket_tier", "delegate_count",
            "discount", "discount_code",
            "pre_tax_amount", "tax_amount", "total_amount", "add_ons_total_amount",
            "currency",
            "company_name", "contact_name", "contact_email", "contact_phone",
            "accounts_contact_email",
            # write-only company address fields
            "company_address", "company_city", "company_state",
            "company_country", "company_postal_code", "company_website",
            "payment_status", "payment_type", "payment_date", "payment_due_date",
            "paid_or_free", "paid_free",
            "request_date", "invoice_date", "booking_code",
            "sales_executive", "sales_executive_name",
            "team_leader", "team_leader_name",
            "reference", "parent_code", "notes", "add_ons",
            "delegates", "event_detail",
            "source", "form_name", "form_url", "packages",
            "created_at", "updated_at", "updated_by", "updated_by_name",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        user = getattr(request, "user", None)
        can_edit = user and (getattr(user, "is_admin", False) or getattr(user, "role", "") == "sales")
        if not can_edit:
            fields["invoice_number"].read_only = True
        return fields

    def to_representation(self, instance):
        from book_delegate.serializers import BookDelegateInlineSerializer
        ret = super().to_representation(instance)
        ret["delegates"] = BookDelegateInlineSerializer(instance.delegates.all(), many=True).data
        request = self.context.get("request")
        if request and not getattr(request.user, "is_admin", False):
            for f in _ADMIN_ONLY_FIELDS:
                ret.pop(f, None)
        return ret

    def _upsert_company(self, validated_data):
        """Extract write-only company fields, run upsert, return Company or None."""
        company_payload = {
            "DelegateCompanyName": validated_data.get("company_name", ""),
            "Address":    validated_data.pop("company_address", ""),
            "City":       validated_data.pop("company_city", ""),
            "StateRegion": validated_data.pop("company_state", ""),
            "Country":    validated_data.pop("company_country", ""),
            "PostalCode": validated_data.pop("company_postal_code", ""),
            "CompanyWebAddress": validated_data.pop("company_website", ""),
        }
        if company_payload["DelegateCompanyName"]:
            from companies.models import Company
            company, _ = Company.get_or_create_from_payload(company_payload)
            return company
        # Still strip the fields even if no name
        for k in ("company_address", "company_city", "company_state",
                   "company_country", "company_postal_code", "company_website"):
            validated_data.pop(k, None)
        return None

    def _apply_event_sales_executive(self, validated_data, instance=None):
        """
        Own the booking with whoever the EVENTS TAB has on its event code.

        The Bookings table's Sales Executive column reads invoice.sales_executive,
        and nothing was setting it outside the website-intake path — so a booking
        entered by hand, or moved to another event, showed no owner at all (or the
        previous event's owner, which is worse than blank).

        Two guards:
          - `sales_executive` explicitly present in the payload wins. Imports and
            the admin can still pin an owner; only an unstated one is derived.
          - On update, nothing happens unless event_code is actually CHANGING. A
            save that leaves the event alone must not silently re-home the booking
            away from a deliberate per-invoice assignment.
        """
        if "sales_executive" in validated_data:
            return
        event_code = validated_data.get("event_code")
        if not event_code:
            return
        if instance is not None and event_code == instance.event_code:
            return
        validated_data["sales_executive"] = BookEvent.auto_assign_sales(event_code)

    def create(self, validated_data):
        from django.db import transaction
        from book_delegate.models import BookDelegate

        delegates_data = validated_data.pop("delegates", [])
        self._upsert_company(validated_data)
        self._apply_event_sales_executive(validated_data)

        with transaction.atomic():
            instance = super().create(validated_data)

            for d_data in delegates_data:
                if not d_data.get("first_name") and d_data.get("full_name"):
                    parts = d_data["full_name"].split(" ", 1)
                    d_data["first_name"] = parts[0]
                    if len(parts) > 1 and not d_data.get("last_name"):
                        d_data["last_name"] = parts[1]
                clean = {k: v for k, v in d_data.items() if k in _ALLOWED_DELEGATE}
                BookDelegate.objects.create(
                    invoice=instance,
                    event_code=instance.event_code,
                    **clean,
                )

            instance.delegate_count = instance.delegates.count()
            instance.save(update_fields=["delegate_count"])

        return instance

    def validate_delegates(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Delegates must be a list.")
        for i, d in enumerate(value):
            if not d.get("first_name") and d.get("full_name"):
                parts = d.get("full_name", "").split(" ", 1)
                d["first_name"] = parts[0]
                if len(parts) > 1 and not d.get("last_name"):
                    d["last_name"] = parts[1]
            if not d.get("first_name") and not d.get("full_name"):
                raise serializers.ValidationError(f"Delegate #{i+1} is missing a name.")
            # Only require email for new delegates (existing ones have an id)
            is_existing = d.get("id") and str(d.get("id")).isdigit()
            if not is_existing:
                if not d.get("email"):
                    raise serializers.ValidationError(f"Delegate #{i+1} is missing an email.")
                if "@" not in d.get("email", ""):
                    raise serializers.ValidationError(f"Delegate #{i+1} has an invalid email.")
        return value

    def update(self, instance, validated_data):
        import logging
        from django.db import transaction
        from book_delegate.models import BookDelegate
        logger = logging.getLogger(__name__)

        delegates_data = validated_data.pop("delegates", None)
        self._upsert_company(validated_data)
        # Before super().update() — a transfer to another event has to re-derive the
        # owner in the same write, or the booking sits under the old event's sales
        # executive until someone notices.
        self._apply_event_sales_executive(validated_data, instance=instance)

        old_invoice_number = instance.invoice_number
        new_invoice_number = validated_data.get("invoice_number", old_invoice_number)

        with transaction.atomic():
            # Cascade invoice_number to delegates BEFORE updating BookEvent so that
            # instance.delegates.all() still resolves correctly after the rename.
            # db_constraint=False on the FK means no DB constraint blocks this.
            if new_invoice_number != old_invoice_number:
                BookDelegate.objects.filter(invoice_id=old_invoice_number).update(
                    invoice_id=new_invoice_number
                )

            instance = super().update(instance, validated_data)

            if delegates_data is not None:
                existing = {d.id: d for d in instance.delegates.all()}
                payload_ids = {
                    int(d["id"]) for d in delegates_data
                    if d.get("id") and str(d.get("id")).isdigit()
                }

                removed_ids = set(existing.keys()) - payload_ids
                if removed_ids:
                    cnt, _ = instance.delegates.filter(id__in=removed_ids).delete()
                    logger.info("DELETED %d delegates from invoice %s", cnt, instance.invoice_number)

                created_count = updated_count = 0
                emails_seen = set()

                for d_data in delegates_data:
                    d_id = d_data.get("id")
                    d_id = int(d_id) if d_id and str(d_id).isdigit() else None
                    clean = {k: v for k, v in d_data.items() if k in _ALLOWED_DELEGATE}

                    if d_id and d_id in existing:
                        # Existing delegate — update by ID, email not required
                        email = d_data.get("email", "").strip().lower()
                        if email and BookDelegate.objects.filter(
                            invoice=instance, email=email
                        ).exclude(id=d_id).exists():
                            raise serializers.ValidationError(
                                {"delegates": f"Email {email} already exists on this invoice."}
                            )
                        BookDelegate.objects.filter(id=d_id).update(**clean)
                        updated_count += 1
                    else:
                        # New delegate — email required and must be unique on this invoice
                        email = d_data.get("email", "").strip().lower()
                        if not email or email in emails_seen:
                            continue
                        emails_seen.add(email)
                        if not BookDelegate.objects.filter(invoice=instance, email=email).exists():
                            BookDelegate.objects.create(
                                invoice=instance,
                                event_code=instance.event_code,
                                **clean,
                            )
                            created_count += 1
                        else:
                            BookDelegate.objects.filter(
                                invoice=instance, email=email
                            ).update(**clean)
                            updated_count += 1

                logger.info(
                    "NESTED UPDATE invoice %s: %d created, %d updated, %d removed",
                    instance.invoice_number, created_count, updated_count, len(removed_ids),
                )
                
                # Ensure all delegates (old and new) match the parent invoice's event_code
                instance.delegates.all().update(event_code=instance.event_code)
                
                instance.delegate_count = instance.delegates.count()
                instance.save(update_fields=["delegate_count"])

        return instance

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

    def get_updated_by_name(self, obj):
        if obj.updated_by_id:
            u = obj.updated_by
            return u.get_full_name() or u.username
        return None

    def get_event_detail(self, obj):
        from events.models import Event
        try:
            ev = Event.objects.get(event_code=obj.event_code)
            return {
                "name": ev.name,
                "city": ev.city,
                "status": ev.event_status,
                "year": ev.event_date.year if ev.event_date else None,
            }
        except Exception:
            return None


class PaymentUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = BookEvent
        fields = ["payment_status", "payment_type", "payment_date", "paid_or_free", "ticket_tier"]

    def validate(self, data):
        status = data.get("payment_status", getattr(self.instance, "payment_status", None))
        if status == "Paid":
            date  = data.get("payment_date", getattr(self.instance, "payment_date", None))
            ptype = data.get("payment_type", getattr(self.instance, "payment_type", None))
            if not date:
                raise serializers.ValidationError(
                    {"payment_date": "payment_date is required when status is Paid."}
                )
            if not ptype:
                raise serializers.ValidationError(
                    {"payment_type": "payment_type is required when status is Paid."}
                )
        return data


# ── Website Intake ─────────────────────────────────────────────────────────────

class DelegatePayloadSerializer(serializers.Serializer):
    FirstName         = serializers.CharField(max_length=150)
    LastName          = serializers.CharField(max_length=150, required=False, default="")
    Email             = serializers.EmailField()
    PhoneNumber       = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    Position          = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    TicketPackage     = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    SponsorshipLevel  = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    TicketTier        = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    PaidOrFree        = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")


class WebsiteBookingSerializer(serializers.Serializer):
    """Validates and maps the Zoho-compatible website payload."""
    # Booking identifiers
    InvoiceNumber = serializers.CharField(max_length=100)
    Eventcode     = serializers.CharField(max_length=50)
    Eventname     = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    Date          = serializers.CharField(required=False, allow_blank=True, default="")
    RequestDate   = serializers.CharField(required=False, allow_blank=True, default="")
    InvoiceDate   = serializers.CharField(required=False, allow_blank=True, default="")
    # Financial amounts
    PreTaxAmount       = serializers.CharField(required=False, allow_blank=True, default="")
    TaxAmount          = serializers.CharField(required=False, allow_blank=True, default="")
    TotalAmount        = serializers.CharField(required=False, allow_blank=True, default="")
    AddOnsTotalAmount  = serializers.CharField(required=False, allow_blank=True, default="")
    Discount           = serializers.CharField(required=False, allow_blank=True, default="0")
    DiscountCode       = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    Currency           = serializers.CharField(max_length=10, required=False, allow_blank=True, default="USD")
    PaymentStatus      = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    # Form metadata
    FormName = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    FormURL  = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    Packages = serializers.JSONField(required=False, default=list)
    # Company
    DelegateCompanyName = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    Address             = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    City                = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    StateRegion         = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    Country             = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    PostalCode          = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    CompanyWebAddress   = serializers.CharField(required=False, allow_blank=True, default="")
    # Contact
    AccountsContactEmail = serializers.EmailField(required=False, allow_blank=True, default="")
    # Invoice-level ticket/payment classification
    TicketTier = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    PaidOrFree = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    PaymentType = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    # Delegates
    Delegates = DelegatePayloadSerializer(many=True, required=False, default=list)

    def _decimal_or_none(self, value, name):
        if not value and value != 0:
            return None
        s_val = str(value).replace(",", "").replace("%", "").strip()
        if not s_val:
            return None
        try:
            return Decimal(s_val)
        except InvalidOperation:
            raise serializers.ValidationError({name: f"Invalid decimal: {value}"})

    def _decimal(self, value, name):
        if not value and value != 0:
            return Decimal("0")
        s_val = str(value).replace(",", "").replace("%", "").strip()
        if not s_val:
            return Decimal("0")
        try:
            return Decimal(s_val)
        except InvalidOperation:
            raise serializers.ValidationError({name: f"Invalid decimal: {value}"})

    def validate_InvoiceNumber(self, value):
        return value.strip()

    def validate_Eventcode(self, value):
        return value.strip().upper()

    def validate(self, data):
        data["Discount"] = self._decimal(data["Discount"], "Discount")
        for f in ("PreTaxAmount", "TaxAmount", "TotalAmount", "AddOnsTotalAmount"):
            data[f] = self._decimal_or_none(data[f], f)
        return data
