"""
pre_event_docs/serializers.py
──────────────────────────────
Only the stored models need one. Every list on the page is built as plain dicts
in services.py, because a projection is not a model and wrapping it in a
Serializer would only re-declare fields the projection just named.
"""
from rest_framework import serializers

from .models import NetworkingPlan


class NetworkingPlanSerializer(serializers.ModelSerializer):
    created_by = serializers.CharField(source="created_by.username",
                                       read_only=True, default="")
    # Both derived on the model, and both are why floor_repeats is stored
    # alongside repeat_pairs; see NetworkingPlan.optimal.
    attendees = serializers.IntegerField(read_only=True)
    largest_table = serializers.IntegerField(read_only=True)
    optimal = serializers.BooleanField(read_only=True)
    rosters = serializers.SerializerMethodField()

    class Meta:
        model = NetworkingPlan
        fields = [
            "id", "event_code", "edition", "rounds", "tables",
            "attendees", "largest_table", "repeat_pairs", "floor_repeats", "optimal",
            "assignment", "rosters", "created_at", "created_by",
        ]

    def get_rosters(self, obj):
        """
        Per table per round, for whoever lays the room out.

        Keys are stringified because JSON object keys are strings anyway, and
        leaving them as ints would have the frontend reading back "3" for a key
        it wrote as 3.
        """
        return [
            {str(table): people for table, people in round_map.items()}
            for round_map in obj.rosters()
        ]
