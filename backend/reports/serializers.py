"""
reports/serializers.py
"""
from rest_framework import serializers
from .models import GoogleSheetSource


# ── Google Sheet Source ────────────────────────────────────────────────────────

class GoogleSheetSourceSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    status_display  = serializers.SerializerMethodField()

    class Meta:
        model  = GoogleSheetSource
        fields = [
            "id", "name", "description",
            "sheet_id", "sheet_url", "worksheet_name", "sheet_type",
            "is_active", "sync_enabled", "sync_frequency",
            "column_mappings", "transformation_config",
            "filter_config", "grouping_config", "formula_config",
            "last_synced_at", "last_successful_sync", "last_failed_sync",
            "sync_status", "status_display", "records_count", "last_error",
            "created_by", "created_by_name",
            "created_at", "updated_at", "notes",
        ]
        read_only_fields = [
            "id", "sync_status", "records_count", "last_error",
            "last_synced_at", "last_successful_sync", "last_failed_sync",
            "created_at", "updated_at",
        ]

    def get_created_by_name(self, obj):
        return obj.created_by.username if obj.created_by_id else None

    def get_status_display(self, obj):
        return obj.get_sync_status_display()

    def create(self, validated_data):
        # Auto-extract sheet ID from URL if a URL was provided
        sheet_url = validated_data.get("sheet_url", "")
        if sheet_url and not validated_data.get("sheet_id"):
            validated_data["sheet_id"] = GoogleSheetSource.extract_sheet_id(sheet_url)
        elif validated_data.get("sheet_id"):
            validated_data["sheet_id"] = GoogleSheetSource.extract_sheet_id(
                validated_data["sheet_id"]
            )
        validated_data["created_by"] = self.context["request"].user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Re-extract if sheet_id or sheet_url is being changed
        if "sheet_id" in validated_data:
            validated_data["sheet_id"] = GoogleSheetSource.extract_sheet_id(
                validated_data["sheet_id"]
            )
        if "sheet_url" in validated_data and not validated_data.get("sheet_id"):
            validated_data["sheet_id"] = GoogleSheetSource.extract_sheet_id(
                validated_data["sheet_url"]
            )
        return super().update(instance, validated_data)


class GoogleSheetSourceListSerializer(serializers.ModelSerializer):
    """Lightweight list serializer — omits large JSON config fields."""
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = GoogleSheetSource
        fields = [
            "id", "name", "sheet_type", "worksheet_name",
            "is_active", "sync_enabled", "sync_frequency",
            "sync_status", "records_count",
            "last_synced_at", "last_error",
            "created_by_name", "created_at",
        ]

    def get_created_by_name(self, obj):
        return obj.created_by.username if obj.created_by_id else None
