"""
proposal_submission/serializers.py
───────────────────────────────────
One serializer for list, detail, create and update — the create and edit field
sets are identical (the frontend shares a single form component for both), so
splitting them would only duplicate the field list.

Field names are the wire contract that frontend/src/api/proposalSubmission.js
depends on; rename here if the underlying column ever changes, never in the
frontend (see reports/serializers.py for the precedent).
"""
from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework import serializers

from accounts.import_common import as_url
from events.name_lookup import EventNameMixin
from webhooks.event_resolver import resolve_event_code
from .access import may_see_mr_fields, may_use_event_code, permitted_event_codes
from .models import ProposalSubmission

# MR-internal fields. The Zoho quickview layout deliberately omits these, and
# they are notes MR writes for MR — readable and writable by admin and market
# research only, and STRIPPED from the payload for everyone else rather than
# blanked, so no other role can tell whether a value exists. Follows the
# _ADMIN_ONLY_FIELDS pattern in book_event/serializers.py.
#
# linkedin_company is also absent from the Zoho quickview, but it is a public
# profile URL, not internal commentary, so it stays visible — the exclusion there
# is layout economy, not confidentiality.
#
# The visibility predicate itself lives in access.py — see may_see_mr_fields.
_MR_ONLY_FIELDS = frozenset([
    "slot_recommendation_mr",
    "internal_footnotes_mr",
])
# Public alias — views.py imports this rather than keeping a second list.
MR_ONLY_FIELDS = _MR_ONLY_FIELDS

# The team works in IST. TIME_ZONE is UTC, so between 00:00 and 05:30 IST the UTC
# date is still yesterday and an early-morning submission would be stamped a day
# early. Storage stays UTC and TIME_ZONE/USE_TZ are untouched — this is a local
# default for one field, not a project-wide change.
BUSINESS_TZ = ZoneInfo("Asia/Kolkata")


def business_today():
    """Today's date as the team experiences it, regardless of server TIME_ZONE."""
    return timezone.now().astimezone(BUSINESS_TZ).date()

# Everything the client may write. Audit columns are appended read-only below.
EDITABLE_FIELDS = [
    "event_code", "submission_date", "participation_type",
    "speaker_name", "email", "company_name",
    "sales_pitch_factor", "presentation_theme",
    "linkedin_speaker", "linkedin_company", "linkedin_followers",
    "speaker_slot_status", "sponsorship_status", "spex_remarks",
    "agenda_slot", "speaking_slot_assignment", "revenue_possibility",
    "panel_approached", "panel_topic", "panel_status",
    "speaker_slot_reoffered", "risk_assessment_live",
    "added_to_agenda",
    "internal_footnotes_mr", "slot_recommendation_mr", "agenda_addition",
]

# The MRE columns. They are the paper review's OUTPUT — PaperReview.proposal_score
# is summed from the six-criterion rubric and grade is derived from it, both
# server-side on every save — so a proposal is a place they are READ, never
# authored. Typing a grade here would put a number on the row that no rubric
# produced, and the qc_score_stale flag exists precisely to surface where the two
# have drifted; letting the proposal be edited would make that flag unreadable.
#
# WHO CAN STILL WRITE THEM, and why that is not a hole:
#   * paper_review/proposal_bridge.py, through serializer.save(**kwargs), exactly
#     as it already passes created_by and source_paper_review. Read-only means
#     "not from a client payload", not "immutable".
#   * the importer, which writes the model directly and must keep carrying QC
#     from the sheet, or a historical load would arrive ungraded.
# Both are server-side and neither takes the value from a request body.
MRE_FIELDS = ("qc_grade", "qc_score")

# Tracker columns read from the event catalogue and the bookings pipeline, not
# stored here. Annotated by ProposalSubmissionViewSet._annotate_tracker_context
# and rendered by the _Annotated* fields below. views.py imports this list rather than
# keeping a second copy; it lives HERE because views already imports from this
# module and the reverse would be circular.
DERIVED_FIELDS = [
    "event_date", "event_status", "production_executive", "spex_manager",
    "booking_date", "payment_date", "booking_status_se",
]

READ_ONLY_FIELDS = [
    "id", "created_at", "updated_at",
    "created_by", "updated_by", "created_by_name", "updated_by_name",
    "duplicate_count",
    # Provenance, set by paper_review/proposal_bridge.py and by nothing else. A
    # client must not be able to claim a proposal came from a review, or to move
    # it to a different one — that would rewrite the audit trail.
    "source_paper_review", "qc_score_stale",
    # C4 — set only by import/commit/, never by the client; see views.py.
    "import_batch_id",
    *DERIVED_FIELDS,
    *MRE_FIELDS,
]


class _LinkField(serializers.URLField):
    """
    A link column that takes anchor markup and stores the address inside it.

    WHY to_internal_value AND NOT validate_<field>
    URLValidator runs inside to_internal_value, and `<a href="…">…</a>` is not a
    valid URL, so a validate_ hook is never reached — the request 400s first with
    "Enter a valid URL" on a cell whose address is sitting right there in the tag.
    Unwrapping has to happen before the validator, which means the field.

    WHY THIS EXISTS AT ALL
    Every other place data enters this module already tolerates anchor markup:
    the importer runs these two columns through as_url (importer.py URL_FIELDS),
    ExtLink in the frontend resolves it for display, and migration 0008 cleaned
    1,876 stored rows of it. The serializer was the one entrance without that
    tolerance, so a value the CSV importer accepts happily was rejected when the
    same text was pasted into the form.

    as_url is shared, not reimplemented — it owns the quoting variations, the
    entity decoding, and the rule that text which is not a link at all passes
    through untouched.
    """

    def to_internal_value(self, data):
        cleaned, error = as_url(data)
        if error:
            raise serializers.ValidationError(f"This link {error}.")
        return super().to_internal_value(cleaned)


class _AnnotationMixin:
    """
    Reads a queryset annotation, and None when there is not one.

    Same contract as get_duplicate_count / get_qc_score_stale, which return None
    on create, duplicate and any other path where the instance never went through
    get_queryset. This exists instead of five more SerializerMethodField pairs
    because all five getters would have been the identical getattr.

    get_attribute is what needs overriding, and only that: the default walks the
    model's attributes and raises on a name the model does not define, and none of
    these five are columns. Serializer.to_representation short-circuits a None
    before calling the field, so the concrete classes below never see one.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("read_only", True)
        super().__init__(**kwargs)

    def get_attribute(self, instance):
        return getattr(instance, self.field_name, None)


class _AnnotatedDate(_AnnotationMixin, serializers.DateField):
    """
    A date annotation, rendered the way every other date on this serializer is.

    NOT a bare Field. A plain passthrough returns the datetime.date object
    itself, which the JSON renderer happens to ISO-format on the way out but
    leaves as a date in serializer.data — so the same field read two ways gave
    two types, and submission_date beside it gave a string either way.
    """


class _AnnotatedText(_AnnotationMixin, serializers.CharField):
    """A text annotation. CharField, so a None stays None rather than becoming ''."""


class ProposalSubmissionSerializer(EventNameMixin, serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()
    # Resolved from the Event catalogue for display, so the list view can show a
    # human-readable event without the client fetching all events just to label
    # a row. Never written.
    #
    # get_event_name comes from EventNameMixin. It used to query the catalogue
    # once per row, which was 500 queries on a 500 row page; the mixin resolves
    # the whole code to name map once per response. See events/name_lookup.py.
    event_name = serializers.SerializerMethodField()
    # READS the queryset annotation added in views._annotate_duplicates — it does
    # NOT query. A method field here is not the N+1 the annotation exists to
    # avoid; it is the only safe way to expose an annotation that is absent on
    # create/duplicate responses, where the instance never went through
    # get_queryset. Returns None there rather than a misleading 0.
    duplicate_count = serializers.SerializerMethodField()
    # A3. Same mechanism as duplicate_count, and for the same reason: it READS the
    # queryset annotation added in views._annotate_stale_qc_score, it does not
    # query. Zoho does not propagate edits from a paper review to the proposal it
    # generated, and neither does this port (both workflows are on-add), so
    # re-scoring a review silently leaves its proposal's qc_score behind. This
    # makes that visible instead. None on create/duplicate responses, where the
    # instance never went through get_queryset — see get_qc_score_stale.
    qc_score_stale = serializers.SerializerMethodField()

    # required=False / allow_blank mirror the model's blank=True, default="",
    # which is what ModelSerializer would have inferred; declaring the field
    # explicitly means restating them. max_length matches the column.
    linkedin_speaker = _LinkField(required=False, allow_blank=True, max_length=500)
    linkedin_company = _LinkField(required=False, allow_blank=True, max_length=500)

    # The tracker's read-only context columns. See DERIVED_FIELDS and
    # _AnnotationMixin above; every one is annotated in the viewset.
    event_date           = _AnnotatedDate()
    event_status         = _AnnotatedText()
    production_executive = _AnnotatedText()
    spex_manager         = _AnnotatedText()
    booking_date         = _AnnotatedDate()
    payment_date         = _AnnotatedDate()
    booking_status_se    = _AnnotatedText()

    class Meta:
        model  = ProposalSubmission
        fields = [*READ_ONLY_FIELDS, "event_name", *EDITABLE_FIELDS]
        read_only_fields = READ_ONLY_FIELDS

    def get_created_by_name(self, obj):
        u = obj.created_by
        return (u.get_full_name() or u.username) if u else None

    def get_updated_by_name(self, obj):
        u = obj.updated_by
        return (u.get_full_name() or u.username) if u else None

    def get_duplicate_count(self, obj):
        return getattr(obj, "duplicate_count", None)

    def get_qc_score_stale(self, obj):
        """
        True when this proposal's qc_score no longer equals the proposal_score of
        the review that generated it. False for manually created rows (nothing to
        drift from).

        FOLLOWS THE duplicate_count APPROACH, deliberately: annotating is right for
        the list path but the annotation is absent on create, duplicate and any
        other path that never went through get_queryset, and computing it there
        would mean a query per object. Returning None on those paths says
        "not evaluated here" rather than asserting a misleading False.
        """
        return getattr(obj, "qc_score_stale", None)

    # ── MR-internal field visibility ──────────────────────────────────────────

    def _request_user(self):
        request = self.context.get("request")
        return getattr(request, "user", None) if request else None

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if not may_see_mr_fields(self._request_user()):
            for f in _MR_ONLY_FIELDS:
                ret.pop(f, None)
        return ret

    def validate(self, attrs):
        """
        Write side of the same rule, and it has to distinguish two cases.

        The shared form posts all 21 keys on every save. Since MR fields are
        stripped from this user's GET, their form holds "" for them — so a plain
        edit of some other column arrives carrying blank MR keys with no intent
        behind them. Rejecting that would make the whole row uneditable for
        everyone outside MR.

        So: a blank/no-op MR key is DROPPED (the stored value survives
        untouched), while an attempt to write actual content is refused loudly.
        Dropping is safe precisely because the field is invisible to this user —
        they cannot have formed an intent about a value they were never shown.
        """
        if may_see_mr_fields(self._request_user()):
            return attrs

        offending = []
        for field in sorted(_MR_ONLY_FIELDS & set(attrs)):
            submitted = attrs[field]
            blank = submitted in (None, "")
            unchanged = (
                self.instance is not None
                and submitted == getattr(self.instance, field, None)
            )
            if blank or unchanged:
                attrs.pop(field)          # form echo, not an edit
            else:
                offending.append(field)

        if offending:
            raise serializers.ValidationError({
                f: "This field is restricted to Market Research and Admin."
                for f in offending
            })
        return attrs

    # ── Validation ────────────────────────────────────────────────────────────

    def validate_event_code(self, value):
        """
        Resolve through webhooks/event_resolver.py — the single source of truth
        for turning a free-text code into an Event. It is not re-implemented
        here: that module owns exact matching, anchored boundary matching, the
        ambiguity rule and the "no fuzzy fallback" guarantee, and a second copy
        of that logic is exactly how the codebase ended up with "BIU" resolving
        to "BIUK - PM".

        A proposal is not a web booking, so accepting_web_bookings is
        deliberately NOT consulted: proposals arrive for events that are not
        selling tickets online. That means BOOKINGS_OFF is a valid resolution
        here, and only genuine no-match and ambiguity are errors.
        """
        code = (value or "").strip()
        if not code:
            raise serializers.ValidationError("Event code is required.")

        resolution = resolve_event_code(code, code)

        # A single matched edition is the answer whether or not web bookings are
        # open on it — resolution.matches holds the winning tier's full set.
        if len(resolution.matches) == 1:
            resolved = resolution.matches[0].event_code
            # Creation is scoped too. Without this, a user could file a proposal
            # against an event outside their assignment and it would vanish from
            # their own list the moment it saved — indistinguishable from data
            # loss. Checked against the RESOLVED code, so an alias cannot smuggle
            # an out-of-scope event past the check.
            user = self._request_user()
            if not may_use_event_code(user, resolved):
                raise serializers.ValidationError(
                    f"You are not assigned to event '{resolved}'. "
                    f"Assigned events: {sorted(permitted_event_codes(user))}"
                )
            return resolved

        if resolution.outcome.name == "AMBIGUOUS" or len(resolution.matches) > 1:
            codes = sorted(e.event_code for e in resolution.matches)
            raise serializers.ValidationError(
                f"Ambiguous event code '{code}' matched {len(codes)} editions: "
                f"{codes}. Use the exact code."
            )

        raise serializers.ValidationError(
            f"No event with code '{code}' exists in the event catalogue. "
            f"Prefilter candidates: {resolution.candidates}"
        )

    def validate_speaker_name(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError("Speaker name is required.")
        return name

    # validate_qc_score is GONE, not moved. qc_score is read-only now (see
    # MRE_FIELDS), so DRF discards a client-supplied value before any field
    # validator could run and the method was unreachable. The floor itself is
    # unchanged and still enforced where writes actually happen: the model's
    # MinValueValidator(0), which the importer's column checks and the mass-update
    # builder both read.

    def validate_linkedin_followers(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("Follower count cannot be negative.")
        return value

    def create(self, validated_data):
        # submission_date defaults to today when the client omits it. The
        # reference detail view carried a date on a record whose intake form
        # field was blank, which points at a server-side default. ASSUMPTION —
        # delete these two lines if submissions should instead stay undated until
        # someone processes them.
        if not validated_data.get("submission_date"):
            validated_data["submission_date"] = business_today()
        return super().create(validated_data)
