"""
proposal_submission/models.py
──────────────────────────────
Inbound speaker / sponsorship proposals against a specific event edition.

Field list derived from the legacy Zoho Creator "Event Management" proposal form.
An MR-driven intake pipeline like Ticket Central, but for proposals rather than
research tickets: who is pitching, how Market Research graded them, which slot
they are up for, and where the speaker slot and the sponsorship each stand.

WHY THE SELECT FIELDS HAVE NO choices=
participation_type, qc_grade, speaker_slot_status, sponsorship_status,
revenue_possibility and the four panel/risk tracker columns are all dropdowns in
the source sheet, but the real picklist values were never confirmed. They are plain CharFields here, with
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
from django.db.models.functions import Lower
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

    # ── Panel track ───────────────────────────────────────────────────────────
    # The agenda tracker's "Panel" header group. A speaker turned down for a solo
    # slot is routinely offered a panel seat instead; that is a SEPARATE decision
    # with its own topic, so it cannot be folded into speaker_slot_status.
    #
    # panel_approached reads as a yes/no question in the sheet, but it is stored
    # as text rather than a BooleanField on purpose: a tracker column of this
    # shape carries a third, not-yet-asked state, and a boolean flattens "no" and
    # "not asked" into the same False. Same no-choices= reasoning as the select
    # fields above; see the module docstring.
    panel_approached = models.CharField(max_length=20, blank=True, default="")
    panel_topic      = models.CharField(max_length=255, blank=True, default="")
    panel_status     = models.CharField(max_length=30, blank=True, default="")

    # A slot that was declined and then put back on the table. Distinct from
    # speaker_slot_status, which says where the slot stands NOW and not whether it
    # has been offered more than once.
    speaker_slot_reoffered = models.CharField(max_length=30, blank=True, default="")

    # "Risk Assesment (Live)" in the tracker, spelled correctly here; the
    # importer accepts the sheet's spelling. 100 rather than the 20-30 the
    # dropdown-shaped columns use, because the vocabulary is unconfirmed and a
    # 30-char column would reject the import outright if this turns out to be a
    # short note rather than a picklist. A wide varchar costs nothing in
    # Postgres; narrowing later is the cheap direction.
    risk_assessment_live = models.CharField(max_length=100, blank=True, default="")

    # THE MRE's RECOMMENDATION, not the assignment. This is where
    # paper_review/proposal_bridge.py writes PaperReview.session_location_on_agenda,
    # and all 1,877 populated rows hold one of the ten session slots, so the column
    # is the paper review's answer to "where should this talk go".
    #
    # The COLUMN NAME is left alone deliberately. Renaming it to
    # slot_recommendation_mre would touch the bridge's FIELD_MAP, the importer's
    # header table, the filter registry, the mass-update registry, every test that
    # names it and a data migration, to change a string the user never sees; the
    # display label carries the meaning instead. Not to be confused with
    # slot_recommendation_mr below, which is MR's free-text NOTE and is stripped
    # from the payload for anyone outside MR.
    agenda_slot         = models.CharField(max_length=150, blank=True, default="")

    # THE AGENDA TEAM'S DECISION, and a separate fact from the recommendation
    # above. The two disagree whenever the team moves a talk, which is the case
    # the tracker exists to make visible; one column could not show both the
    # suggestion and what was actually done with it.
    #
    # Same width and same vocabulary as agenda_slot, so a recommendation can be
    # accepted by copying it across without truncation.
    speaking_slot_assignment = models.CharField(max_length=150, blank=True, default="")

    # 50, not the original 20. The confirmed vocabulary includes
    # "Genuine clasg(INV sent)" at 23 characters and "Withdrawn before INV" at
    # exactly 20, so the old width could not store one value and had no room
    # above another. A too-narrow column here is not a validation error, it is a
    # psycopg DataError inside import_commit's transaction that rolls back the
    # whole 500-row chunk — see accounts/import_common.py:column_errors.
    revenue_possibility = models.CharField(max_length=50, blank=True, default="")

    # ── Internal MR notes ─────────────────────────────────────────────────────
    internal_footnotes_mr  = models.TextField(blank=True, default="")
    slot_recommendation_mr = models.TextField(blank=True, default="")

    # The reference detail view renders this with headings and what look like
    # topic tags. The intake form exposes one plain textarea, so it is stored as
    # one blob and rendered preformatted. If those tags turn out to be a separate
    # computed field, an industry_tags column can be added without touching this.
    agenda_addition = models.TextField(blank=True, default="")

    # A CHECKBOX, and a different fact from agenda_addition directly above.
    # agenda_addition is the session outline, prose the team writes; this records
    # whether the speaker actually made it onto the published agenda. A row can
    # easily have one without the other, in both directions, so they cannot share
    # a column.
    added_to_agenda = models.BooleanField(default=False, blank=True)

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
            # The duplicate lookup, (event_code, lower(email)). This table has no
            # email index at all, so before this the correlated Subquery in
            # ProposalSubmissionViewSet._annotate_duplicates rechecked every
            # event_code match against the heap, once per row. Measured on the
            # current database, counting the rows with a duplicate went from
            # 119 ms to 12 ms. Same index, same reasoning, as
            # paper_review_dupe_idx; the two modules stay parallel.
            models.Index(
                "event_code", Lower("email"), name="proposal_dupe_idx",
            ),
            # Meta.ordering above is exactly ["-submission_date", "-id"], and this
            # index is exactly that. Same reasoning, same shape, as
            # paper_reviews_subdate_id_idx.
            models.Index(
                fields=["-submission_date", "-id"],
                name="proposal_subs_subdate_id_idx",
            ),
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
