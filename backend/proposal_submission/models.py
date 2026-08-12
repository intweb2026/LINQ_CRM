"""
proposal_submission/models.py
──────────────────────────────
Inbound speaker / sponsorship proposals against a specific event edition.

Field list derived from the legacy Zoho Creator "Event Management" proposal form.
An MR-driven intake pipeline like Ticket Central, but for proposals rather than
research tickets: who is pitching, how Market Research graded them, which slot
they are up for, and where the speaker slot and the sponsorship each stand.

WHY THE SELECT FIELDS HAVE NO choices=
participation_type, qc_grade, speaker_slot_status, sponsorship_status and
revenue_possibility are all dropdowns in Zoho, but the real picklist values were
not legible in the reference screenshots. They are plain CharFields here, with
the candidate options offered by the frontend only. That is deliberate: a wrong
guess baked into choices= rejects a legitimate value with a 400 and turns every
correction into a migration, and it would make a historical Zoho import fail on
values we never saw. Ticket_central made the same call for priority /
type_of_ticket / relationship (see the D4 notes in ticket_central/models.py) —
this follows that precedent. Add choices= once the true lists are confirmed.

TWO STATUSES, NO STATE MACHINE
There is no single overarching status. A proposal can be considered as a speaker,
as a sponsor, or both, so speaker_slot_status and sponsorship_status move
independently. No transition guards exist (unlike Ticket's MR→DMD submit
actions) — plain PATCH is the whole workflow.
"""
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ProposalSubmission(models.Model):

    # ── Event ─────────────────────────────────────────────────────────────────
    # Indexed CharField, not an FK. Every cross-app reference to an event in this
    # codebase is a code string (BookEvent.event_code, Ticket.event_code); a hard
    # FK here would be the only one and would break the shared filter/search
    # conventions. Existence IS enforced, but in the serializer — see
    # serializers.py: validate_event_code.
    event_code = models.CharField(max_length=50, db_index=True)

    submission_date = models.DateField(null=True, blank=True)

    # ── Who is proposing ──────────────────────────────────────────────────────
    participation_type = models.CharField(max_length=50, blank=True, default="")
    speaker_name       = models.CharField(max_length=150)
    email              = models.EmailField()
    company_name       = models.CharField(max_length=255, blank=True, default="")

    # ── Market Research scoring ───────────────────────────────────────────────
    # Grade and score are independent, both set by hand. The score's scale is
    # unknown (the one reference record read grade "B" with score 27), so nothing
    # derives one from the other and no upper bound is imposed — only >= 0.
    qc_grade = models.CharField(max_length=10, blank=True, default="")
    qc_score = models.IntegerField(null=True, blank=True,
                                   validators=[MinValueValidator(0)])

    # Named like a score but a plain text box on the reference form. Left as text
    # until someone confirms otherwise.
    sales_pitch_factor = models.CharField(max_length=255, blank=True, default="")
    presentation_theme = models.CharField(max_length=255, blank=True, default="")

    # ── LinkedIn ──────────────────────────────────────────────────────────────
    # 500 rather than URLField's default 200: real LinkedIn profile URLs carry
    # long tracking segments and truncation would corrupt them silently.
    linkedin_speaker   = models.URLField(max_length=500, blank=True, default="")
    linkedin_company   = models.URLField(max_length=500, blank=True, default="")
    linkedin_followers = models.PositiveIntegerField(null=True, blank=True)

    # ── Outcome ───────────────────────────────────────────────────────────────
    speaker_slot_status = models.CharField(max_length=30, blank=True, default="")
    sponsorship_status  = models.CharField(max_length=30, blank=True, default="")
    spex_remarks        = models.TextField(blank=True, default="")

    # Free text in the reference data ("Day 1, Afternoon Session"), not a
    # structured date/time.
    agenda_slot         = models.CharField(max_length=150, blank=True, default="")
    revenue_possibility = models.CharField(max_length=20, blank=True, default="")

    # ── Internal MR notes ─────────────────────────────────────────────────────
    internal_footnotes_mr  = models.TextField(blank=True, default="")
    slot_recommendation_mr = models.TextField(blank=True, default="")

    # The reference detail view renders this with headings and what look like
    # topic tags. The intake form exposes one plain textarea, so it is stored as
    # one blob and rendered preformatted. If those tags turn out to be a separate
    # computed field, an industry_tags column can be added without touching this.
    agenda_addition = models.TextField(blank=True, default="")

    # ── Provenance ────────────────────────────────────────────────────────────
    # Set ONLY on rows auto-created from a paper review (see
    # paper_review/proposal_bridge.py). Manually created, imported and duplicated
    # rows leave it null, so "where did this proposal come from" has an answer
    # instead of an inference.
    #
    # SET_NULL, not CASCADE: deleting a review must not delete the proposal it
    # generated — the proposal is a live pipeline record with its own slot and
    # sponsorship state, and losing it would be data loss triggered from another
    # module. It goes back to reading as manually created, which is the truthful
    # degraded state.
    source_paper_review = models.ForeignKey(
        "paper_review.PaperReview", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="proposal_submissions",
        db_index=True,
    )

    # C4. All rows written by ONE call to import/commit/ share one value, minted
    # by import/preview/ and echoed back through every chunk of the same file —
    # see views.py:import_preview / import_commit. Null on every row that did not
    # come from the importer (created via the form, duplicate(), or the paper
    # review bridge).
    #
    # WHY THIS EXISTS: chunked commits (MAX_ROWS=500 per call) mean a failure in
    # chunk 3 of a 1500-row file leaves chunks 1-2 already committed with no way
    # to name what landed. This id is that name — "everything with
    # import_batch_id=<uuid>" is answerable after the fact via the filter below,
    # without needing an undo path (none is built here; identifying a batch and
    # undoing it are different jobs).
    import_batch_id = models.UUIDField(null=True, blank=True, db_index=True)

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_proposal_submissions",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="updated_proposal_submissions",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "proposal_submissions"
        # -id is a tiebreaker, not decoration: submission_date is non-unique and
        # nullable, and LIMIT/OFFSET over a non-unique sort both repeats and
        # SKIPS rows. See accounts/ordering.py — StableOrderingFilter appends the
        # pk for API requests, and this keeps direct ORM use deterministic too.
        ordering = ["-submission_date", "-id"]
        indexes = [
            models.Index(fields=["event_code"]),
            models.Index(fields=["submission_date"]),
            models.Index(fields=["speaker_slot_status"]),
            models.Index(fields=["sponsorship_status"]),
            models.Index(fields=["created_at"]),
        ]
        # A7. One paper review generates at most ONE proposal, enforced by the
        # database rather than only by the guard in proposal_bridge.py — a retried
        # request or a second code path must not be able to mint a second row for
        # the same review. Partial, so the many manually-created rows can all keep
        # a NULL here.
        constraints = [
            models.UniqueConstraint(
                fields=["source_paper_review"],
                condition=Q(source_paper_review__isnull=False),
                name="one_auto_proposal_per_paper_review",
            ),
        ]
        verbose_name = "Proposal Submission"
        verbose_name_plural = "Proposal Submissions"

    def __str__(self):
        return f"{self.speaker_name} — {self.event_code}"
