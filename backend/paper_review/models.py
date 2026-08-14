"""
paper_review/models.py
───────────────────────
Speaker paper / abstract reviews scored against a six-criterion rubric.

Sibling of proposal_submission: same MR-driven intake shape, same RBAC model,
same conventions. Where the two differ it is noted inline.

THE RUBRIC
Six criteria with individual maxima summing to 45:

    closeness_to_topic            10
    closeness_to_region            5
    clear_solution_to_challenges  10
    case_study_results_examples    5
    not_obvious_sales_pitch        5
    company_profile_score         10
                                  ──
                                  45

Verified against a real record: 9 + 2 + 9 + 1 + 1 + 5 = 27.

company_profile_score is a NUMERIC sub-score of that rubric. It is not related to
company_name and is not a "company profile" text field.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

# (field, maximum). The single source of truth for the rubric — the serializer's
# bounds, the computed total and the tests all read this rather than repeating
# the numbers.
CRITERIA = (
    ("closeness_to_topic",           10),
    ("closeness_to_region",           5),
    ("clear_solution_to_challenges", 10),
    ("case_study_results_examples",   5),
    ("not_obvious_sales_pitch",       5),
    ("company_profile_score",        10),
)
CRITERIA_FIELDS = tuple(name for name, _ in CRITERIA)
CRITERIA_MAX = dict(CRITERIA)
RUBRIC_TOTAL = sum(CRITERIA_MAX.values())      # 45


def _criterion(maximum):
    return models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(maximum)],
    )


class PaperReview(models.Model):

    # ── Submission ────────────────────────────────────────────────────────────
    paper_submission_date = models.DateField(null=True, blank=True)

    # Indexed CharField, not an FK — every cross-app event reference in this
    # codebase is a code string. Existence is enforced in the serializer, which
    # stores the catalogue's canonical spelling so the exact-match RBAC scope
    # keeps working. See paper_review/event_codes.py.
    event_code = models.CharField(max_length=50, db_index=True)

    # NOT user-entered. In Zoho these cache the notification recipients resolved
    # from the event; they are read-only in the serializer and absent from the
    # form. A later pass populates them server-side at send time.
    speaker_email_ref  = models.EmailField(blank=True, default="")
    research_email_ref = models.EmailField(blank=True, default="")

    # ── Speaker ───────────────────────────────────────────────────────────────
    speaker_name = models.CharField(max_length=150)
    company_name = models.CharField(max_length=255, blank=True, default="")
    email        = models.EmailField(db_index=True)

    # 500, not URLField's 200 default: real LinkedIn URLs carry long tracking
    # segments and would truncate silently. Same call as proposal_submission.
    linkedin_speaker   = models.URLField(max_length=500, blank=True, default="")
    linkedin_company   = models.URLField(max_length=500, blank=True, default="")
    linkedin_followers = models.PositiveIntegerField(null=True, blank=True)

    # "NOS?" is a checkbox in the Zoho form and its business meaning is unknown.
    # Stored as a plain boolean with NO logic built on it, deliberately.
    nos = models.BooleanField(default=False, blank=True)

    # ── Rubric ────────────────────────────────────────────────────────────────
    closeness_to_topic           = _criterion(10)
    closeness_to_region          = _criterion(5)
    clear_solution_to_challenges = _criterion(10)
    case_study_results_examples  = _criterion(5)
    not_obvious_sales_pitch      = _criterion(5)
    company_profile_score        = _criterion(10)

    # COMPUTED. Recomputed on every write from the six criteria and never taken
    # from the client — summing is arithmetic, so deriving it server-side is
    # safe. Null criteria are EXCLUDED rather than counted as zero, and when all
    # six are null this stays null: an unscored review must read as unscored, not
    # as 0/45.
    proposal_score = models.PositiveSmallIntegerField(null=True, blank=True)

    # MANUAL, typed by the reviewer. Deliberately NOT derived from a percentage:
    # the ≥80/≥60/≥40 bands are inferred from a single record and are not
    # confirmed business rules. Matches the standing decision that
    # proposal_submission's qc_grade and qc_score are independent and manual.
    #
    # WIDTH. This was max_length=1, written on the assumption that a grade is a
    # single letter. The real vocabulary is not. The Zoho export carries A, B, B+,
    # C, D and E, and 'B+' is the third most common grade in it, 355 of 3492 rows.
    # At one character every one of those rows raised a DataError on import;
    # because the commit writes a chunk inside one transaction.atomic(), that
    # failure discarded the other 499 rows with it. 5 leaves room for a modifier on
    # any letter without inviting free text, and stays under qc_grade's 10; see the
    # assertion in tests_paper_to_proposal.py that this column must not be wider
    # than the one proposal_bridge copies it into.
    grade = models.CharField(max_length=5, blank=True, default="", db_index=True)

    # ── Outcome ───────────────────────────────────────────────────────────────
    session_location_on_agenda = models.CharField(max_length=100, blank=True, default="")

    # MR-internal. Stripped from the payload entirely for anyone outside
    # MR/Admin — see serializers.py. REVERSIBLE: drop the field from
    # _MR_ONLY_FIELDS in serializers.py and the three guards in views.py to make
    # it visible to everyone; no migration is involved.
    internal_footnotes = models.TextField(blank=True, default="")
    # NOT restricted — this is written for the speaker to read.
    feedback_to_speaker = models.TextField(blank=True, default="")

    # Rich text (WYSIWYG) in Zoho. Stored as plain text: this design system has
    # no rich-text editor, so markup would render as literal angle brackets.
    proposal_received = models.TextField(blank=True, default="")
    theme             = models.CharField(max_length=255, blank=True, default="")
    agenda_addition   = models.TextField(blank=True, default="")

    # ── Provenance ────────────────────────────────────────────────────────────
    # All rows written by ONE call to import/commit/ share one value, minted by
    # import/preview/ and echoed back through every chunk of the same file. Null on
    # every row created through the form.
    #
    # Chunked commits (MAX_ROWS=500 per call) mean a failure in chunk 3 of a
    # 1500-row file leaves chunks 1-2 already committed with no way to name what
    # landed. This id is that name. Mirrors
    # ProposalSubmission.import_batch_id exactly, including having no undo
    # endpoint: identifying a batch and reversing one are different jobs.
    import_batch_id = models.UUIDField(null=True, blank=True, db_index=True)

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_paper_reviews",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="updated_paper_reviews",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "paper_reviews"
        # -id is a tiebreaker, not decoration: paper_submission_date is
        # non-unique AND nullable, and LIMIT/OFFSET over a non-unique sort both
        # repeats and SKIPS rows. StableOrderingFilter appends the pk for API
        # requests; this keeps direct ORM use deterministic too.
        #
        # DIVERGENCE: the Zoho report defaulted to Added_Time descending. This
        # follows the spec's paper_submission_date instead; ?ordering=-created_at
        # reproduces the Zoho order for anyone who wants it.
        ordering = ["-paper_submission_date", "-id"]
        indexes = [
            models.Index(fields=["event_code"]),
            models.Index(fields=["paper_submission_date"]),
            models.Index(fields=["grade"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["email"]),
            # The duplicate lookup, which is (event_code, lower(email)) and is
            # therefore served by neither of the two plain indexes above: the
            # email one cannot answer lower(email), and event_code alone leaves
            # roughly 35 rows per probe to be rechecked against the heap.
            #
            # Everything that reads duplicate_count pays for this. The Subquery in
            # PaperReviewViewSet._annotate_duplicates runs once per row, so a
            # whole-table pass is 7,080 of these probes. Measured on the current
            # database, ?has_duplicates=true went from 389 ms to 24 ms, and so did
            # ?ordering=-duplicate_count; both are one click in the table.
            #
            # Column order matters. event_code first is the more selective of the
            # two here and keeps the index usable for event_code-only lookups,
            # which is what the existing event_code index already served.
            models.Index(
                "event_code", Lower("email"), name="paper_review_dupe_idx",
            ),
        ]
        verbose_name = "Paper Review"
        verbose_name_plural = "Paper Reviews"

    def computed_score(self):
        """
        Sum of the filled criteria, or None when none is filled.

        Excluding nulls rather than zeroing them is the difference between
        "scored 0 on that criterion" and "not yet scored", and the two must not
        collapse into the same number.
        """
        values = [getattr(self, name) for name in CRITERIA_FIELDS]
        filled = [v for v in values if v is not None]
        return sum(filled) if filled else None

    def save(self, *args, **kwargs):
        self.proposal_score = self.computed_score()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.speaker_name} — {self.event_code}"


class NotificationLog(models.Model):
    """
    One row per attempt to tell the production team about a new paper review.

    WHY NOT ActionLog
    ActionLog is flat — user, a 255-char action string and a free-text details
    blob. "Which addresses actually resolved, was this the fallback, and did the
    send fail" is structured data that has to be queryable months later
    ("did production ever get told about this speaker?"), and stuffing it into a
    prose string means the answer exists but cannot be filtered for.

    paper_review is SET_NULL rather than CASCADE, and `subject` is stored
    alongside it, deliberately: deleting a review must not erase the evidence of
    who was notified, and the subject line carries the event code and speaker name
    so an orphaned row still says what it was about.
    """

    class Status(models.TextChoices):
        RESOLVED   = "resolved",   "Resolved"
        FALLBACK   = "fallback",   "Fallback"
        FAILED     = "failed",     "Failed"
        # settings.PAPER_REVIEW_NOTIFICATIONS_ENABLED was False: recipients were
        # resolved and the body was rendered, but nothing was sent — see B1.
        SUPPRESSED = "suppressed", "Suppressed"

    paper_review = models.ForeignKey(
        PaperReview, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="notifications", db_index=True,
    )
    subject = models.CharField(max_length=255, blank=True, default="")

    # The addresses the send was actually attempted with. JSON rather than a
    # comma-joined string so "was arthur@ on this?" is a containment query and not
    # a substring guess.
    to_addresses = models.JSONField(default=list, blank=True)
    cc_addresses = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, db_index=True,
    )
    # Carries the failure reason on `failed`, the failed resolution step on
    # `fallback`, and a degradation note on an otherwise-`resolved` send (e.g. the
    # event had no sales executive but assigned users covered it).
    error = models.TextField(blank=True, default="")

    # B8's MR rule is auditable rather than assumed: this records whether
    # internal_footnotes actually went out in the body.
    included_internal_footnotes = models.BooleanField(default=False)

    sent_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "paper_review_notification_logs"
        ordering = ["-sent_at", "-id"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["sent_at"]),
        ]
        verbose_name = "Paper Review Notification"
        verbose_name_plural = "Paper Review Notifications"

    def __str__(self):
        return f"{self.status} · {self.subject}"
