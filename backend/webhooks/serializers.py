"""
webhooks/serializers.py
"""
from rest_framework import serializers
from .models import WebhookApiKey, WebhookLog


# ── API Key ────────────────────────────────────────────────────────────────────

def validate_form_link(attrs, instance=None):
    """
    A paper review form link names a reviewer; nothing else may.

    Checked on create AND on partial update, against the EFFECTIVE values rather
    than the submitted ones: a PATCH that sets only `target` would otherwise turn
    an ordinary ingest key into a reviewer-less form link, and one that sets only
    `mre` would hang a reviewer off a booking key where nothing reads it.
    """
    form = WebhookApiKey.Target.PAPER_REVIEW_FORM
    sentinel = object()

    target = attrs.get("target", sentinel)
    if target is sentinel:
        target = getattr(instance, "target", "")
    mre = attrs.get("mre", sentinel)
    if mre is sentinel:
        mre = getattr(instance, "mre", None)

    if target == form and mre is None:
        raise serializers.ValidationError({
            "mre": "A paper review form link must name the reviewer it belongs to.",
        })
    if mre is not None and target != form:
        raise serializers.ValidationError({
            "mre": "Only a paper review form link names a reviewer; "
                   "set the destination to that, or clear the reviewer.",
        })
    return attrs


class WebhookApiKeySerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    key_preview     = serializers.SerializerMethodField()
    mre_name        = serializers.SerializerMethodField()
    # The path this key posts to, resolved through urls.py by the model. Served
    # so the keys page can build a working URL without holding a copy of any
    # webhook path in JavaScript, which is how its "copy test URL" button came
    # to hand out the booking URL for every key regardless of destination.
    ingest_path     = serializers.SerializerMethodField()

    class Meta:
        model  = WebhookApiKey
        fields = [
            "id", "name", "api_key", "key_preview", "event",
            "target", "ingest_path",
            # Set only on a PAPER_REVIEW_FORM key — the reviewer whose public
            # form this link opens. See paper_review/public_form.py.
            "mre", "mre_name",
            "is_active", "allowed_domains", "notes",
            "created_by", "created_by_name",
            "created_at", "last_used_at", "usage_count",
        ]
        read_only_fields = ["id", "api_key", "created_at", "last_used_at", "usage_count"]

    def validate(self, attrs):
        return validate_form_link(attrs, self.instance)

    def get_created_by_name(self, obj):
        return obj.created_by.username if obj.created_by_id else None

    def get_mre_name(self, obj):
        if not obj.mre_id:
            return None
        return obj.mre.get_full_name() or obj.mre.username

    def get_ingest_path(self, obj):
        return obj.ingest_path()

    def get_key_preview(self, obj):
        k = obj.api_key or ""
        if len(k) <= 12:
            return k
        return k[:10] + "…" + k[-4:]


class WebhookApiKeyCreateSerializer(serializers.ModelSerializer):
    """Used on creation — auto-generates the key."""

    class Meta:
        model  = WebhookApiKey
        fields = ["name", "event", "target", "mre", "is_active",
                  "allowed_domains", "notes"]

    def validate(self, attrs):
        return validate_form_link(attrs)

    def create(self, validated_data):
        validated_data["api_key"]    = WebhookApiKey.generate_key()
        validated_data["created_by"] = self.context["request"].user
        return super().create(validated_data)


# ── Webhook Log ────────────────────────────────────────────────────────────────

class WebhookLogListSerializer(serializers.ModelSerializer):
    """Lightweight — no full payload/headers/stack_trace for list performance."""
    api_key_name = serializers.SerializerMethodField()
    created_booking_number = serializers.SerializerMethodField()

    class Meta:
        model = WebhookLog
        fields = [
            "id", "api_key", "api_key_name",
            "source", "ip_address",
            "status", "http_status", "processing_status",
            "db_insert_status",
            "invoice_number", "event_code", "event_name",
            "error_message",
            "retry_count",
            "records_inserted", "records_updated", "records_failed",
            "created_delegates_count",
            "received_at", "processing_started_at", "processed_at",
            "processing_duration",
            "created_booking", "created_booking_number",
            "created_at",
        ]

    def get_api_key_name(self, obj):
        return obj.api_key.name if obj.api_key_id else None

    def get_created_booking_number(self, obj):
        return obj.created_booking.invoice_number if obj.created_booking_id else None


class WebhookLogSerializer(WebhookLogListSerializer):
    """Full detail — includes payload, headers, stack_trace, processing_notes."""

    class Meta(WebhookLogListSerializer.Meta):
        fields = WebhookLogListSerializer.Meta.fields + [
            "payload", "headers", "response",
            "stack_trace", "processing_notes",
        ]
