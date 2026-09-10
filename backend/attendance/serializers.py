from rest_framework import serializers

from .models import AttendanceRecord


class AttendanceRecordSerializer(serializers.ModelSerializer):
    """
    The log, read-only, and it reads WITHOUT a join.

    Every attendee column is the snapshot stored on the row, so a record whose
    delegate has since been deleted still names who arrived. Only the operator's
    name is resolved through a relation, and select_related in the viewset pays
    for it once per page.
    """
    checked_in_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceRecord
        fields = [
            "id", "delegate", "event_code", "edition",
            "attendee_type", "attendee_name", "attendee_email", "company_name",
            "status", "checked_in_at", "checked_in_by", "checked_in_by_name",
            "source",
        ]
        read_only_fields = fields

    def get_checked_in_by_name(self, obj):
        user = obj.checked_in_by
        if not user:
            return ""
        return (user.get_full_name() or user.username or "").strip()
