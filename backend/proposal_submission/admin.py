from django.contrib import admin

from .models import ProposalSubmission


@admin.register(ProposalSubmission)
class ProposalSubmissionAdmin(admin.ModelAdmin):
    list_display  = ("id", "speaker_name", "company_name", "event_code",
                     "participation_type", "qc_grade", "qc_score",
                     "speaker_slot_status", "sponsorship_status", "submission_date")
    list_filter   = ("participation_type", "qc_grade",
                     "speaker_slot_status", "sponsorship_status", "revenue_possibility")
    search_fields = ("speaker_name", "email", "company_name", "event_code",
                     "presentation_theme")
    date_hierarchy = "submission_date"
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by",
                      "source_paper_review", "import_batch_id")
