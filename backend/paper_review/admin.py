"""
paper_review/admin.py
──────────────────────
PaperReview follows proposal_submission's admin conventions.

NotificationLog is registered READ-ONLY on purpose (B7): it is the evidence trail
for "did production actually get told about this speaker?", and an audit record an
admin can edit or delete answers that question with whatever the last editor
preferred. Rows are written by paper_review/notifications.py and by nothing else.
"""
from django.contrib import admin

from .models import NotificationLog, PaperReview


@admin.register(PaperReview)
class PaperReviewAdmin(admin.ModelAdmin):
    list_display  = ("id", "speaker_name", "company_name", "event_code",
                     "proposal_score", "grade", "session_location_on_agenda",
                     "paper_submission_date")
    list_filter   = ("grade", "nos", "session_location_on_agenda")
    search_fields = ("speaker_name", "email", "company_name", "event_code", "theme")
    date_hierarchy = "paper_submission_date"
    readonly_fields = ("proposal_score", "speaker_email_ref", "research_email_ref",
                       "created_at", "updated_at", "created_by", "updated_by")


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display  = ("id", "sent_at", "status", "subject", "to_addresses",
                     "cc_addresses", "included_internal_footnotes")
    list_filter   = ("status", "included_internal_footnotes")
    search_fields = ("subject", "error")
    date_hierarchy = "sent_at"
    readonly_fields = tuple(f.name for f in NotificationLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
