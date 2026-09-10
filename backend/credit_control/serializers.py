"""
credit_control/serializers.py
──────────────────────────────
Read shapes for the queue and the drill-down, and the one write shape.

The lead row is assembled from three places: the lead itself, the invoice it
points at, and the HubSpot contact cache. None of that is denormalised onto the
lead, because all three move independently and a copy would be a fourth thing
to keep in step.
"""
from rest_framework import serializers

from hubspot.models import HubSpotContact

from .engine import EDITABLE_FIELDS, normalize_disposition
from .models import CreditControlDisposition, CreditControlLead


class DispositionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CreditControlDisposition
        fields = ["id", "label", "category", "status_group", "sort_order", "active"]


class DelegateSerializer(serializers.Serializer):
    """
    One delegate under an invoice, for the row's drill-down.

    Read-only and deliberately thin. The queue is a list of invoices, and this
    is what opens underneath one when it carries more than a single delegate.
    """
    name = serializers.SerializerMethodField()
    job_title = serializers.CharField(source="position")
    email = serializers.CharField()
    phone = serializers.CharField(source="phone_number")
    delegate_number = serializers.IntegerField()
    paid_or_free = serializers.SerializerMethodField()
    ticket_tier = serializers.SerializerMethodField()
    add_ons = serializers.CharField()

    def get_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()

    def get_paid_or_free(self, obj):
        return obj.delegate_paid_or_free or (
            obj.invoice.paid_or_free if obj.invoice_id else ""
        )

    def get_ticket_tier(self, obj):
        return obj.delegate_ticket_tier or (
            obj.invoice.ticket_tier if obj.invoice_id else ""
        )


class LeadSerializer(serializers.ModelSerializer):
    """
    One row of a caller's queue.

    `who_we_call` is the accounts contact, because that is who releases the
    money, with the delegate address behind it as the fallback and also shown
    so the caller can see both. The amount is deliberately absent: the callers
    already know it, so it is not a column.
    """
    # DataTable identifies a row by `id`: it keys the React list on it, tracks
    # selection with it, and now tracks which row is drilled into with it. These
    # rows had only `invoice_number`, so every id was undefined — every key
    # collided, and opening one drill-down opened all of them, because
    # `has(undefined)` is true for every row at once.
    id = serializers.CharField(source="invoice_id", read_only=True)
    invoice_number = serializers.CharField(source="invoice_id", read_only=True)
    event_code = serializers.CharField(source="invoice.event_code", read_only=True)
    booking_code = serializers.CharField(source="invoice.booking_code", read_only=True)
    company = serializers.CharField(source="invoice.company_name", read_only=True)
    invoice_date = serializers.DateField(source="invoice.invoice_date", read_only=True)
    payment_status = serializers.CharField(source="invoice.payment_status", read_only=True)
    payment_date = serializers.DateField(source="invoice.payment_date", read_only=True)

    client_name = serializers.SerializerMethodField()
    who_we_call = serializers.SerializerMethodField()
    who_we_call_url = serializers.SerializerMethodField()
    delegate_email = serializers.SerializerMethodField()
    delegate_count = serializers.SerializerMethodField()
    delegates = serializers.SerializerMethodField()

    assigned_to_name = serializers.SerializerMethodField()
    disposition = serializers.SerializerMethodField()
    status_group = serializers.SerializerMethodField()
    days_pending = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()
    phone_mobile = serializers.SerializerMethodField()
    phone_direct = serializers.SerializerMethodField()
    phone_main = serializers.SerializerMethodField()
    times_called = serializers.SerializerMethodField()
    my_calls = serializers.SerializerMethodField()
    last_call_at = serializers.SerializerMethodField()
    last_call_seconds = serializers.SerializerMethodField()
    lead_type = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    handed_off_from_name = serializers.SerializerMethodField()
    handoff_reason = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()
    resolved_by_name = serializers.SerializerMethodField()
    effective_invoice_type = serializers.CharField(read_only=True)

    class Meta:
        model = CreditControlLead
        fields = [
            "id",
            "invoice_number", "event_code", "booking_code", "company",
            "invoice_date", "payment_status", "payment_date",
            "client_name", "who_we_call", "who_we_call_url",
            "delegate_email", "delegate_count", "delegates",
            "phone", "phone_mobile", "phone_direct", "phone_main",
            "times_called", "my_calls", "last_call_at", "last_call_seconds",
            "lead_type",
            "can_edit",
            "resolved_at", "resolved_by", "resolved_by_name",
            "resolved_after_effort", "resolved_disposition",
            "bucket", "assigned_to", "assigned_to_name",
            "disposition", "status_group", "remark", "callback_date",
            "days_pending", "last_touched_at",
            "resolved", "done_reason",
            "invoice_type", "invoice_type_override", "effective_invoice_type",
            "invoice_type_source", "invoice_type_basis", "invoice_type_quote",
            "invoice_type_confidence", "invoice_type_error",
            "handoff_brief", "handed_off_at", "first_seen",
            "handed_off_from", "handed_off_from_name",
            "handoff_disposition", "handoff_remark", "handoff_reason",
            "history",
        ]
        read_only_fields = [f for f in fields if f not in EDITABLE_FIELDS]

    # The maps and the phone cache are passed in by the view, once per request,
    # rather than looked up per row. A per-row lookup here is the classic way a
    # list endpoint acquires N+1 queries.
    @property
    def _groups(self):
        return self.context.get("status_groups") or {}

    @property
    def _contacts(self):
        return self.context.get("contacts") or {}

    def _account_email(self, obj):
        invoice = obj.invoice
        return (
            invoice.accounts_contact_email
            or invoice.contact_email
            or ""
        ).strip().lower()

    def get_client_name(self, obj):
        """
        The person the invoice is about, which is what the row should open with.

        The invoice's own contact name first, then the first delegate's. The
        company is its own column now: a row that leads with a company name
        makes every list of bookings from one organisation look identical,
        while the person is what distinguishes them.
        """
        invoice = obj.invoice
        if invoice.contact_name and invoice.contact_name.strip():
            return invoice.contact_name.strip()
        first = getattr(obj, "_first_delegate", None)
        if first:
            return f"{first.first_name} {first.last_name}".strip()
        return ""

    def get_who_we_call(self, obj):
        return self._account_email(obj) or self.get_delegate_email(obj)

    def get_who_we_call_url(self, obj):
        """
        The HubSpot record for whoever we call, or "" when there is not one.

        Built from the cached contact id, so the link is only offered when the
        contact actually exists; a link to a record that is not there is worse
        than no link, because it costs a page load to find out.
        """
        contact = self._contact(obj)
        if not contact or not contact.contact_id:
            return ""
        from django.conf import settings

        portal = getattr(settings, "HUBSPOT_PORTAL_ID", "") or ""
        if not portal:
            return ""
        return f"https://app.hubspot.com/contacts/{portal}/contact/{contact.contact_id}"

    def get_delegate_email(self, obj):
        first = getattr(obj, "_first_delegate", None)
        return (first.email if first else "").strip().lower()

    def get_delegate_count(self, obj):
        return getattr(obj, "_delegate_count", 0)

    def get_delegates(self, obj):
        rows = getattr(obj, "_delegates", None)
        if not rows:
            return []
        return DelegateSerializer(rows, many=True).data

    def get_assigned_to_name(self, obj):
        user = obj.assigned_to
        if not user:
            return ""
        return user.get_full_name() or user.username

    def get_disposition(self, obj):
        return normalize_disposition(obj.disposition)

    def get_status_group(self, obj):
        label = normalize_disposition(obj.disposition)
        if not label:
            from . import constants
            return constants.GROUP_NOT_ATTEMPTED
        return self._groups.get(label, "")

    def get_days_pending(self, obj):
        return obj.days_pending()

    def _contact(self, obj):
        return self._contacts.get(self.get_who_we_call(obj))

    def get_phone(self, obj):
        contact = self._contact(obj)
        return contact.best_phone if contact else ""

    def get_phone_mobile(self, obj):
        contact = self._contact(obj)
        return contact.mobile_phone if contact else ""

    def get_phone_direct(self, obj):
        contact = self._contact(obj)
        return contact.direct_phone if contact else ""

    def get_phone_main(self, obj):
        contact = self._contact(obj)
        return contact.phone if contact else ""

    def get_times_called(self, obj):
        contact = self._contact(obj)
        # None, not 0. A contact whose count has never been fetched must not
        # render as one that has never been called.
        return contact.times_called if contact else None

    def get_my_calls(self, obj):
        """
        How many times THIS caller has rung THIS lead.

        A DIFFERENT NUMBER FROM times_called, and both are wanted. times_called
        is what HubSpot holds against the contact, so it counts every call by
        anybody, including a sales exec chasing the booking months ago.
        my_calls counts this viewer's own attempts on this invoice, which is the
        one that answers "have I already tried them today".

        Read from CreditControlTouch, one row per logged call, which is what a
        touch is. Zero and not None when nothing is there: unlike the HubSpot
        count, this table is the CRM's own and an absent row genuinely means no
        call was logged, rather than meaning nobody has looked.
        """
        return (self.context.get("my_calls") or {}).get(obj.pk, 0)

    def get_last_call_at(self, obj):
        contact = self._contact(obj)
        return contact.last_call_at if contact else None

    def get_last_call_seconds(self, obj):
        contact = self._contact(obj)
        # Zero is a real answer, a call that connected to nothing, so it must
        # survive as 0 rather than collapsing into null with "never looked".
        return contact.last_call_seconds if contact else None

    def get_can_edit(self, obj):
        """
        May the person asking record work on this lead?

        Mirrors the server's own rule exactly (see views._may_edit), so the
        table renders an input only where a save would actually be accepted. A
        control that looks editable and then 403s is worse than no control.
        """
        if self.context.get("may_edit_all"):
            return True
        return obj.assigned_to_id == self.context.get("viewer_id")

    def get_resolved_by_name(self, obj):
        user = obj.resolved_by
        if not user:
            return ""
        return user.get_full_name() or user.username

    def get_handed_off_from_name(self, obj):
        user = obj.handed_off_from
        if not user:
            return ""
        return user.get_full_name() or user.username

    def get_handoff_reason(self, obj):
        """
        Why this lead moved, in the words of the rule that moved it.

        Derived rather than stored: there is exactly one reason a lead is handed
        over, and it is that the first caller had not got a case in flight by
        day four. Storing a sentence would be storing a paraphrase of a rule
        that lives in the engine, and the two would drift.
        """
        if not obj.handed_off_at:
            return ""
        label = obj.handoff_disposition
        if not label:
            return (
                "Passed on at day four with no contact made, so nobody had "
                "reached them yet."
            )
        return (
            f"Passed on at day four. The previous caller logged {label}, which "
            "left nothing in flight, so it moved to even out the chasing."
        )

    def get_history(self, obj):
        """
        Every edit anybody made, newest first.

        The append-only CreditControlTouch log, which is the real answer to
        "what happened on this lead": the frozen handover fields are a summary,
        and this is the record. Capped at twenty, because a lead worked for
        months would otherwise make a list endpoint heavy for the sake of a
        panel nobody scrolls that far into.
        """
        rows = getattr(obj, "_history", None)
        if rows is None:
            return []
        return [
            {
                "at": t.created_at,
                "by": (t.user.get_full_name() or t.user.username) if t.user else "",
                "disposition": t.disposition,
                "remark": t.remark,
                "changed": t.fields_changed,
            }
            for t in rows
        ]

    def get_lead_type(self, obj):
        """
        Speaker, Delegate or Add-Ons, from the booking code.

        Derived rather than stored, because the booking code is the only truth
        and a copy would drift the first time one is corrected. Same precedence
        the aged debtor table uses, so the queue and the dashboard can never
        disagree about what a row is.
        """
        from .engine import is_addons, is_speaker

        code = obj.invoice.booking_code
        if is_addons(code):
            return "Add-Ons"
        if is_speaker(code):
            return "Speaker"
        return "Delegate"


class LeadWriteSerializer(serializers.Serializer):
    """
    The only writable shape, and it is four fields wide.

    Ownership, bucket, resolved and everything the classifier decides are
    absent on purpose: a caller cannot move a lead to another queue or mark it
    paid by sending a field, whatever the request body says. The one exception
    lives on its own action, because it is a different permission question.
    """
    disposition = serializers.CharField(
        required=False, allow_blank=True, max_length=100,
    )
    remark = serializers.CharField(required=False, allow_blank=True)
    callback_date = serializers.DateField(required=False, allow_null=True)

    def validate_disposition(self, value):
        label = normalize_disposition(value)
        if not label:
            return ""
        valid = set(
            CreditControlDisposition.objects
            .filter(active=True)
            .values_list("label", flat=True)
        )
        if label not in valid:
            raise serializers.ValidationError(
                f"Not an active disposition. Choose one of: {', '.join(sorted(valid))}."
            )
        return label


class InvoiceTypeOverrideSerializer(serializers.Serializer):
    """The human verdict on a speaker invoice. Blank clears it back to the model's."""
    invoice_type_override = serializers.ChoiceField(
        choices=CreditControlLead.InvoiceType.choices, allow_blank=True,
    )
