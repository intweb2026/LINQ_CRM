"""
paper_review/proposal_bridge.py
────────────────────────────────
PART A — the Django port of the Zoho workflow `Paper_to_Proposal_Submiss`
(record event = on add): every new PaperReview mints exactly one
ProposalSubmission.

WHY THIS IS NOT `insert into`
The Deluge writes the row with `insert into Proposal_Submission[...]`, which
bypasses the form entirely: no event-code resolution, no field constraints, no
required-field checks. Replicating that here would let a proposal exist whose
event_code was never resolved through the catalogue — and since RBAC scope is
exact-match membership on event_code, an unresolved code is a row invisible to its
own author. So this goes through ProposalSubmissionSerializer, the same path the
proposal form uses, and a validation failure is a hard error rather than a
silently malformed row (see A4/A5).

THE MAPPING
Taken field-for-field from the Deluge, translated to Django names. Two deliberate
divergences, both flagged rather than silent:

  * linkedin_company IS mapped. Zoho maps linkedin_speaker and
    linkedin_followers but omits the company URL, while mapping every other
    LinkedIn field — an oversight in the script, not a rule. Both target and
    source are URLField(500); nothing is lost by carrying it.
  * internal_footnotes is NOT mapped to internal_footnotes_mr. Zoho leaves the
    MR block blank on the generated row and so does this. Carrying it would also
    push MR-restricted content through a serializer the author may not be allowed
    to write MR fields on.

Blank on the generated row, exactly as in Zoho: participation_type,
sales_pitch_factor, speaker_slot_status, sponsorship_status, spex_remarks,
revenue_possibility, internal_footnotes_mr, slot_recommendation_mr.
"""
from django.db.models import CharField, TextField

from proposal_submission.access import scope_queryset
from proposal_submission.models import ProposalSubmission
from proposal_submission.serializers import (
    MRE_FIELDS, ProposalSubmissionSerializer,
)

from .models import PaperReview

# (proposal field, paper review field). The ONE definition of the mapping — the
# payload builder, the width audit and the tests all read this rather than
# repeating 13 pairs.
FIELD_MAP = (
    ("submission_date",    "paper_submission_date"),
    ("event_code",         "event_code"),
    ("speaker_name",       "speaker_name"),
    ("company_name",       "company_name"),
    ("email",              "email"),
    ("linkedin_speaker",   "linkedin_speaker"),
    ("linkedin_company",   "linkedin_company"),
    ("linkedin_followers", "linkedin_followers"),
    ("qc_score",           "proposal_score"),
    ("qc_grade",           "grade"),
    ("agenda_slot",        "session_location_on_agenda"),
    ("agenda_addition",    "agenda_addition"),
    ("presentation_theme", "theme"),
)

# Left untouched on the generated row. Named so the test can assert they are still
# blank instead of trusting the absence of a mapping line.
LEFT_BLANK = (
    "participation_type", "sales_pitch_factor", "speaker_slot_status",
    "sponsorship_status", "spex_remarks", "revenue_possibility",
    "internal_footnotes_mr", "slot_recommendation_mr",
    # The agenda tracker's own columns. A paper review records how the ABSTRACT
    # scored; whether the speaker was then offered a panel seat, re-offered a
    # declined slot, or judged a live risk are decisions the agenda team takes
    # afterwards, and none of them has a source on the review to copy from. Blank
    # is the correct starting state, not a gap in the mapping.
    "panel_approached", "panel_topic", "panel_status",
    "speaker_slot_reoffered", "risk_assessment_live",
    # A checkbox, so its untouched value is False rather than "" — see the
    # blankness assertion in tests_paper_to_proposal.py, which compares against
    # each field's own default for exactly this reason. Whether a speaker reached
    # the published agenda is decided long after the paper is scored.
    "added_to_agenda",
    # The agenda team's slot ASSIGNMENT. The review's recommendation is mapped,
    # into agenda_slot, by FIELD_MAP above; what the team then does with that
    # recommendation is theirs to record, and pre-filling it would erase the
    # distinction between "suggested" and "assigned" on the very first save.
    "speaking_slot_assignment",
)


class ProposalBridgeError(Exception):
    """
    The proposal could not be created. Raised so the caller's atomic block rolls
    the paper review back too — a review whose proposal silently failed is a gap
    nobody would notice (A5).

    `errors` carries the serializer's own error dict when there is one.
    """

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.message = message
        self.errors = errors or {}


def _max_length(model, field_name):
    field = model._meta.get_field(field_name)
    if isinstance(field, TextField):        # TextField subclasses nothing narrower
        return None                         # unbounded
    if isinstance(field, CharField):
        return field.max_length
    return None


def narrower_targets():
    """
    A6. Mapped pairs where the DESTINATION column is narrower than the SOURCE, so
    a legal paper-review value could not survive the copy.

    Computed from the models at call time, not hardcoded: the point is that a
    future migration narrowing one of these columns is caught by a test rather
    than discovered as truncated data. Returns
    [(proposal_field, review_field, target_max, source_max), …] — empty today.
    """
    out = []
    for target, source in FIELD_MAP:
        t_max = _max_length(ProposalSubmission, target)
        s_max = _max_length(PaperReview, source)
        if t_max is not None and s_max is not None and t_max < s_max:
            out.append((target, source, t_max, s_max))
    return out


def build_payload(review):
    """
    The mapped values a CLIENT payload may carry, as the serializer's `data`.

    MRE_FIELDS are held back — qc_grade and qc_score are read-only on the
    proposal serializer now, so leaving them here would mean DRF silently
    discarded them and every generated proposal arrived ungraded. They are passed
    to serializer.save() instead; see build_server_values and the call site.
    """
    return {target: getattr(review, source) for target, source in FIELD_MAP
            if target not in MRE_FIELDS}


def build_server_values(review):
    """
    The mapped values that only the SERVER may set, as serializer.save() kwargs.

    Read from the same FIELD_MAP as build_payload, so the pair covers the mapping
    exactly once between them and the drift guard in
    tests_paper_to_proposal.py still reads one definition.
    """
    return {target: getattr(review, source) for target, source in FIELD_MAP
            if target in MRE_FIELDS}


def duplicate_peer_count(proposal, user):
    """
    Other proposals sharing (email, event_code) inside this user's scope.

    A9: paper review is the main generator of proposals, so a second review for
    the same speaker and event WILL trip this. It is advisory only and must never
    block — nothing here raises, and there is no unique constraint on that pair.
    Same query as ProposalSubmissionViewSet._duplicate_peer_count, so the number
    the review response reports and the number the proposal list reports agree.
    """
    return (
        scope_queryset(ProposalSubmission.objects.all(), user)
        .filter(email__iexact=proposal.email, event_code=proposal.event_code)
        .exclude(pk=proposal.pk).count()
    )


def create_proposal_for_review(review, request):
    """
    Create the one ProposalSubmission for `review`. Returns
    (proposal, created: bool, peer_count: int).

    MUST be called inside the caller's transaction.atomic() — it raises
    ProposalBridgeError on any failure so the review rolls back with it.

    A7: idempotent. A review that already has a proposal returns the existing row
    with created=False, so a retried request, a re-save or any future duplicate
    action cannot silently mint a second one. The partial unique constraint on
    ProposalSubmission.source_paper_review backs this at the database level.
    """
    existing = ProposalSubmission.objects.filter(source_paper_review=review).first()
    if existing is not None:
        return existing, False, duplicate_peer_count(existing, request.user)

    serializer = ProposalSubmissionSerializer(
        data=build_payload(review), context={"request": request},
    )
    if not serializer.is_valid():
        raise ProposalBridgeError(
            "The paper review was not saved: its proposal submission failed "
            "validation.",
            errors=serializer.errors,
        )

    # created_by / source_paper_review are read-only on the serializer and are
    # passed here, the same way the proposal viewset stamps created_by. The MRE
    # pair joins them for the same reason: the rubric produced those numbers, so
    # they are the server's to write and never a client's.
    proposal = serializer.save(
        created_by=request.user, source_paper_review=review,
        **build_server_values(review),
    )

    # A4. The review's event_code was already resolved to the catalogue's
    # canonical spelling; the proposal serializer resolves independently, so if
    # the two ever disagree the RBAC scope of the two rows would differ and the
    # proposal could be invisible to the person who just created the review.
    # Asserted rather than assumed, and it rolls the pair back.
    if proposal.event_code != review.event_code:
        raise ProposalBridgeError(
            f"Event code resolved differently for the generated proposal: "
            f"review '{review.event_code}' vs proposal "
            f"'{proposal.event_code}'. Nothing was saved."
        )

    return proposal, True, duplicate_peer_count(proposal, request.user)
