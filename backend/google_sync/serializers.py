from rest_framework import serializers

from services.google_sheets import extract_spreadsheet_id
from sync import catalog

from .models import GoogleSheetSyncLog, SheetSyncTarget


class GoogleSheetSyncLogSerializer(serializers.ModelSerializer):
    duration_display = serializers.ReadOnlyField()

    class Meta:
        model = GoogleSheetSyncLog
        fields = [
            "id", "sync_type", "sheet_name",
            "status", "sync_mode",
            "started_at", "completed_at", "duration_seconds", "duration_display",
            "records_processed", "records_created", "records_updated", "records_failed",
            "triggered_by", "trigger_source",
            "error_message", "sync_summary",
            "last_synced_record_id", "last_synced_at",
            "created_at",
        ]


class SheetSyncTargetSerializer(serializers.ModelSerializer):
    """
    A saved push, validated against the catalogue at write time.

    Every way of getting a target wrong is caught here rather than at run time,
    because the person who can fix it is looking at the form now and will not be
    looking at the sync log tomorrow.
    """
    created_by_username = serializers.CharField(source="created_by.username",
                                                read_only=True, default="")
    module_label  = serializers.SerializerMethodField()
    column_labels = serializers.SerializerMethodField()

    class Meta:
        model  = SheetSyncTarget
        fields = [
            "id", "name",
            "spreadsheet_id", "tab_name",
            "module", "module_label", "columns", "column_labels",
            "is_enabled",
            "last_synced_at", "last_status", "last_error", "records_synced",
            "created_by_username", "created_at", "updated_at",
        ]
        read_only_fields = [
            "last_synced_at", "last_status", "last_error", "records_synced",
            "created_at", "updated_at",
        ]

    # ── Display ───────────────────────────────────────────────────────────────

    def _catalogue_columns(self, obj):
        try:
            return {c["key"]: c["label"] for c in catalog.columns_for(obj.module)}
        except catalog.CatalogError:
            return {}

    def get_module_label(self, obj):
        for module in catalog.list_modules():
            if module["key"] == obj.module:
                return module["label"]
        return obj.module

    def get_column_labels(self, obj):
        """
        Labels in the target's own column order, so the UI shows the sheet's
        header row rather than the catalogue's ordering.
        """
        labels = self._catalogue_columns(obj)
        return [labels.get(k, k) for k in (obj.columns or [])]

    # ── Validation ────────────────────────────────────────────────────────────

    def validate_spreadsheet_id(self, value):
        """A pasted URL is accepted and reduced to its id."""
        sheet_id = extract_spreadsheet_id(value)
        if not sheet_id:
            raise serializers.ValidationError("A spreadsheet id or sheet URL is required.")
        return sheet_id

    def validate_tab_name(self, value):
        tab = (value or "").strip()
        if not tab:
            raise serializers.ValidationError("A tab name is required.")
        return tab

    def validate(self, attrs):
        """
        Module and columns are checked together, since a column key only means
        anything relative to its module.
        """
        module  = attrs.get("module",  getattr(self.instance, "module",  None))
        columns = attrs.get("columns", getattr(self.instance, "columns", None))

        if columns is not None and not isinstance(columns, list):
            raise serializers.ValidationError({"columns": "Expected a list of column keys."})

        try:
            catalog.validate(module, columns or [])
        except catalog.CatalogError as exc:
            field = "module" if "Unknown module" in str(exc) else "columns"
            raise serializers.ValidationError({field: str(exc)})

        return attrs
