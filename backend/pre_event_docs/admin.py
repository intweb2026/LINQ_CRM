"""
pre_event_docs/admin.py
────────────────────────
WHERE THE BADGE LOG LIVES, and how to look at it.

Table   pre_event_docs_badge_issues
Admin   /admin/pre_event_docs/badgeissue/
SQL     select event_code, count(*), max(issued_at)
          from pre_event_docs_badge_issues group by 1 order by 3 desc;

This replaces the workbook's Database_Sent tab, which was plain pasted values
with an empty Stamp column. Every row here records one badge as it was PRINTED,
so the Additional Name Badges report can diff it against the check-in sheet.

Read only on purpose. A badge run is written by the print action and removed by
its undo; hand-editing a row here would make the log disagree with what is
physically on the badge table, which is the one thing it exists to record.
"""
from django.contrib import admin

from .models import BadgeIssue, NetworkingPlan


@admin.register(BadgeIssue)
class BadgeIssueAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "event_code", "edition",
                    "issued_at", "issued_by", "run_id")
    list_filter = ("event_code", "issued_at", "issued_by")
    search_fields = ("name", "company", "event_code", "run_id")
    date_hierarchy = "issued_at"
    ordering = ("-issued_at",)
    list_select_related = ("issued_by", "delegate")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(NetworkingPlan)
class NetworkingPlanAdmin(admin.ModelAdmin):
    list_display = ("event_code", "edition", "created_at", "created_by",
                    "attendees", "tables", "rounds", "repeat_pairs",
                    "floor_repeats", "optimal")
    list_filter = ("event_code", "created_at")
    search_fields = ("event_code",)
    ordering = ("-created_at",)
    list_select_related = ("created_by",)

    # attendees and optimal are properties, so they are display-only here.
    readonly_fields = ("assignment",)

    def has_add_permission(self, request):
        return False
