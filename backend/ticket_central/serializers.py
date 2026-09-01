"""
ticket_central/serializers.py
──────────────────────────────
Phase-specific serializers enforce which fields are writable at each stage.
"""
from rest_framework import serializers
from .models import Ticket
from .constants import MR_FIELDS, DMD_FIELDS
from .utils import display_name as _name


class TicketListSerializer(serializers.ModelSerializer):
    created_by_name       = serializers.SerializerMethodField()
    mr_submitted_by_name  = serializers.SerializerMethodField()
    dmd_submitted_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = Ticket
        fields = [
            # Identifiers
            "id", "ticket_number", "external_id", "event_code", "event_name",
            "status", "created_at", "updated_at",
            # MR section — all fields used in COLUMNS
            "purpose", "type_of_ticket", "competitor_event_name", "organizer",
            "event_month_year", "event_location", "relationship",
            "priority", "estimate", "assigned_mr", "link_url",
            "linkedin_keywords", "duplicate_tickets", "mr_comments",
            # DMD section — all fields used in COLUMNS
            "assign_name", "assign_date", "ticket_type",
            "actual_number", "new_contacts_created", "mined_count",
            "complete_date", "hubspot_entry_date", "dm_comments",
            "assign_name_lx2", "actual_count_lx2", "complete_date_lx2",
            "dm_comments_lx2", "source_spreadsheet_id", "source_tab",
            "source_row_number", "idempotency_key",
            # Audit / method fields.
            # added_user_text is Zoho's "Added User" (D16) and return_reason/
            # returned_at are what a returned ticket was actually sent back for —
            # both are columns/fields the Ticket Central table and edit form show,
            # and the list endpoint is the only place the UI reads a ticket from
            # (it never fetches the detail route), so they have to be here.
            "added_user_text", "return_reason", "returned_at",
            "created_by_name", "mr_submitted_by_name", "dmd_submitted_by_name",
            "mr_submitted_at", "dmd_submitted_at",
        ]

    def get_created_by_name(self, obj):       return _name(obj.created_by)
    def get_mr_submitted_by_name(self, obj):  return _name(obj.mr_submitted_by)
    def get_dmd_submitted_by_name(self, obj): return _name(obj.dmd_submitted_by)


class TicketDetailSerializer(serializers.ModelSerializer):
    """Full read-only detail — both sections visible."""
    created_by_name       = serializers.SerializerMethodField()
    mr_submitted_by_name  = serializers.SerializerMethodField()
    dmd_submitted_by_name = serializers.SerializerMethodField()
    assigned_mr_name      = serializers.SerializerMethodField()
    assign_name_name      = serializers.SerializerMethodField()
    assign_name_lx2_name  = serializers.SerializerMethodField()
    returned_by_name      = serializers.SerializerMethodField()

    class Meta:
        model  = Ticket
        fields = "__all__"

    def get_fields(self):
        # Detail view is fully read-only — both sections visible, none writable.
        fields = super().get_fields()
        for f in fields.values():
            f.read_only = True
        return fields

    def get_created_by_name(self, obj):       return _name(obj.created_by)
    def get_mr_submitted_by_name(self, obj):  return _name(obj.mr_submitted_by)
    def get_dmd_submitted_by_name(self, obj): return _name(obj.dmd_submitted_by)
    def get_assigned_mr_name(self, obj):      return obj.assigned_mr or None  # CharField (D4)
    def get_assign_name_name(self, obj):      return obj.assign_name or None  # CharField (D4)
    def get_assign_name_lx2_name(self, obj):  return obj.assign_name_lx2 or None  # CharField (D4)
    def get_returned_by_name(self, obj):      return _name(obj.returned_by)


class TicketCreateSerializer(serializers.ModelSerializer):
    """MR creates a new ticket — only shared + MR fields writable."""

    id = serializers.IntegerField(read_only=True)

    class Meta:
        model  = Ticket
        # D9: no ticket_number — assigned by the overnight backfill job.
        # "id" read-only so callers can reference the created ticket.
        fields = ["id", *MR_FIELDS, "event_code", "event_name"]
        # D8: Purpose and Type of Ticket are required at MR submit.
        extra_kwargs = {
            "purpose":        {"required": True, "allow_blank": False},
            "type_of_ticket": {"required": True, "allow_blank": False},
        }

    def validate(self, data):
        for f in DMD_FIELDS:
            if f in self.initial_data:
                raise serializers.ValidationError(
                    {f: "This field belongs to the Data Mining section."}
                )
        return data

    def validate_purpose(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Purpose is required.")
        return value.strip()

    def validate_type_of_ticket(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Type of Ticket is required.")
        return value.strip()

    def create(self, validated_data):
        from django.utils import timezone
        from .utils import extract_type_code, extract_purpose_code, assign_next_ticket_number

        # A webhook delivery (webhooks/views.py TicketIngestionView) carries no
        # logged-in user, and AnonymousUser is not something an FK will accept,
        # so the creator columns are left NULL for it. Every request from the UI
        # is authenticated and behaves exactly as before.
        user = getattr(self.context.get("request"), "user", None)
        if not getattr(user, "is_authenticated", False):
            user = None
        validated_data["created_by"] = user
        # "Added User" is Zoho's name for who put the row in, and it is a column
        # on the Ticket Central table. It was only ever filled by an import, so
        # every ticket raised in this CRM showed it blank. A webhook delivery has
        # no user, so that case keeps whatever the payload sent.
        if user and not validated_data.get("added_user_text"):
            validated_data["added_user_text"] = _name(user)

        # Submit directly as MR Submitted — no draft step.
        validated_data["status"] = Ticket.Status.MR_SUBMITTED
        validated_data["mr_submitted_by"] = user
        validated_data["mr_submitted_at"] = timezone.now()

        purpose_code = extract_purpose_code(validated_data.get("purpose", ""))
        type_code = extract_type_code(validated_data.get("type_of_ticket", ""))

        if purpose_code:
            validated_data["ticket_number"] = assign_next_ticket_number(
                purpose_code, type_code
            )

        return super().create(validated_data)


class TicketMRUpdateSerializer(serializers.ModelSerializer):
    """MR edits their section — DMD fields blocked."""

    class Meta:
        model  = Ticket
        fields = [*list(MR_FIELDS), "event_code", "event_name"]  # includes priority

    def validate(self, data):
        ticket = self.instance
        if ticket.status not in (Ticket.Status.DRAFT, Ticket.Status.RETURNED):
            raise serializers.ValidationError(
                "MR fields can only be edited when ticket is Draft or Returned."
            )
        for f in DMD_FIELDS:
            if f in self.initial_data:
                raise serializers.ValidationError(
                    {f: "This field belongs to the Data Mining section."}
                )
        return data


class TicketDMDUpdateSerializer(serializers.ModelSerializer):
    """DMD edits their section — MR fields blocked."""

    class Meta:
        model  = Ticket
        fields = list(DMD_FIELDS)

    def validate(self, data):
        ticket = self.instance
        if ticket.status != Ticket.Status.MR_SUBMITTED:
            raise serializers.ValidationError(
                "DMD fields can only be edited after MR submission."
            )
        for f in MR_FIELDS:
            if f in self.initial_data:
                raise serializers.ValidationError(
                    {f: "This field belongs to the Market Research section."}
                )
        return data


class TicketAdminUpdateSerializer(serializers.ModelSerializer):
    """
    Admin override: can write any MR or DMD field at any status.
    Status transitions still go through @action endpoints to preserve audit trail.
    """

    class Meta:
        model  = Ticket
        fields = [*MR_FIELDS, *DMD_FIELDS, "event_code", "event_name"]
    # No field-ownership validation, no status guard — admin trust.
    # Status field deliberately omitted — only @action endpoints transition status.
