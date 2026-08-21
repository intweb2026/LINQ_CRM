"""
paper_review/serializers.py
────────────────────────────
One serializer for list, detail, create and update — the field sets are identical
and the frontend shares a single form component, so splitting would only duplicate
a 25-field list.
"""
from rest_framework import serializers

from events.name_lookup import EventNameMixin

from .access import may_see_mr_fields, may_use_event_code, permitted_event_codes
from .event_codes import canonical_matches, resolve_paper_event_code
from .models import CRITERIA_MAX, PaperReview

# MR-internal. Stripped from the payload entirely for anyone outside MR/Admin —
# absent, not blanked, so no other role can tell whether a value exists. Follows
# the _ADMIN_ONLY_FIELDS pattern in book_event/serializers.py.
#
# feedback_to_speaker is NOT here: it is written for the speaker to read.
# REVERSIBLE: empty this set (and drop the three guards in views.py) to make
# internal_footnotes visible to everyone. No migration involved.
_MR_ONLY_FIELDS = frozenset(["internal_footnotes"])
MR_ONLY_FIELDS = _MR_ONLY_FIELDS

# Set by the server, never by the client. Both are recomputed in
# PaperReview.save(), so a client-supplied value is not merely refused — it
# is overwritten.
COMPUTED_FIELDS = ["proposal_score", "grade"]
# Cached notification recipients, resolved from the event by a later pass.
SERVER_OWNED_FIELDS = ["speaker_email_ref", "research_email_ref"]

EDITABLE_FIELDS = [
    "paper_submission_date", "event_code",
    "speaker_name", "company_name", "email",
    "linkedin_speaker", "linkedin_company", "linkedin_followers", "nos",
    "closeness_to_topic", "closeness_to_region", "clear_solution_to_challenges",
    "case_study_results_examples", "not_obvious_sales_pitch",
    "company_profile_score",
    "session_location_on_agenda",
    "internal_footnotes", "feedback_to_speaker",
    "proposal_received", "theme", "agenda_addition",
]

READ_ONLY_FIELDS = [
    "id", "created_at", "updated_at",
    "created_by", "updated_by", "created_by_name", "updated_by_name",
    *COMPUTED_FIELDS, *SERVER_OWNED_FIELDS,
    # Provenance, set only by import/commit/ — never by a client.
    "import_batch_id",
    # C1 — reads the queryset annotation, never queries. See get_duplicate_count.
    "duplicate_count",
]

# Required at the SERIALIZER level (marked * in the Zoho form). The model keeps
# these blank-able so historical imports can land incomplete rows.
REQUIRED_FIELDS = [
    "paper_submission_date", "event_code", "speaker_name", "company_name",
    "email", "linkedin_speaker", "linkedin_followers",
    "closeness_to_topic", "closeness_to_region", "clear_solution_to_challenges",
    "case_study_results_examples", "not_obvious_sales_pitch",
    "company_profile_score",
    "session_location_on_agenda", "proposal_received", "theme",
    "agenda_addition",
]


class PaperReviewSerializer(EventNameMixin, serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()
    # get_event_name comes from EventNameMixin. It used to query the catalogue
    # once per row, which was 500 queries on a 500 row page and 7,080 on a full
    # table walk; the mixin resolves the whole code to name map once per
    # response. See events/name_lookup.py.
    event_name      = serializers.SerializerMethodField()
    # The rubric denominator, so the form need not hardcode 45.
    rubric_total    = serializers.SerializerMethodField()
    # C1. READS the queryset annotation added in views._annotate_duplicates — it
    # does NOT query. A method field here is not the N+1 the annotation exists to
    # avoid; it is the only safe way to expose an annotation that is absent on
    # create responses, where the instance never went through get_queryset.
    # Returns None there rather than a misleading 0 — the duplicate_count
    # precedent in proposal_submission/serializers.py, followed deliberately.
    duplicate_count = serializers.SerializerMethodField()

    class Meta:
        model  = PaperReview
        fields = [*READ_ONLY_FIELDS, "event_name", "rubric_total", *EDITABLE_FIELDS]
        read_only_fields = READ_ONLY_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Applied here rather than per-field so the required list stays one
        # readable block. blank=True on the model would otherwise let "" through.
        for name in REQUIRED_FIELDS:
            field = self.fields.get(name)
            if field is None:
                continue
            field.required = True
            if hasattr(field, "allow_blank"):
                field.allow_blank = False
            if hasattr(field, "allow_null"):
                field.allow_null = False

    def get_created_by_name(self, obj):
        u = obj.created_by
        return (u.get_full_name() or u.username) if u else None

    def get_updated_by_name(self, obj):
        u = obj.updated_by
        return (u.get_full_name() or u.username) if u else None

    def get_rubric_total(self, obj):
        return sum(CRITERIA_MAX.values())

    def get_duplicate_count(self, obj):
        return getattr(obj, "duplicate_count", None)

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
        Write side of the MR rule, with the same echo-vs-edit distinction as
        proposal_submission.

        The shared form posts every key on each save. Since internal_footnotes is
        stripped from this user's GET, their form holds "" for it — so a plain
        edit of some other column arrives carrying a blank MR key with no intent
        behind it. Rejecting that would make the whole row uneditable outside MR.

        So a blank/no-op MR key is DROPPED (the stored value survives) while an
        attempt to write real content is refused loudly. Dropping is safe
        precisely because the field is invisible to this user — they cannot have
        formed an intent about a value they were never shown.
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
        Resolve through paper_review/event_codes.py, which layers spacing
        tolerance over webhooks/event_resolver.py without weakening the anchored
        boundary rule. The canonical catalogue spelling is stored, so the
        exact-match RBAC scope keeps working.

        BOOKINGS_OFF counts as SUCCESS: paper reviews arrive for events that are
        not selling tickets online, so `matches` — not `.event` — is the signal.
        """
        code = (value or "").strip()
        if not code:
            raise serializers.ValidationError("Event code is required.")

        # A spacing collision in the catalogue itself ("AFS-JS" AND "AFS - JS"
        # both stored) is genuinely ambiguous and must not be guessed.
        spacing_collision = canonical_matches(code)
        if len(spacing_collision) > 1:
            raise serializers.ValidationError(
                f"Event code '{code}' matches {len(spacing_collision)} catalogue "
                f"entries that differ only in spacing: {spacing_collision}. "
                f"Use the exact Event.event_code value."
            )

        resolution = resolve_paper_event_code(code)
        matches = resolution.matches

        if len(matches) == 1:
            resolved = matches[0].event_code
            user = self._request_user()
            if not may_use_event_code(user, resolved):
                raise serializers.ValidationError(
                    f"You are not assigned to event '{resolved}'. "
                    f"Assigned events: {sorted(permitted_event_codes(user))}"
                )
            return resolved

        if len(matches) > 1:
            codes = sorted(e.event_code for e in matches)
            raise serializers.ValidationError(
                f"Ambiguous event code '{code}' matched {len(codes)} editions: "
                f"{codes}. Use the exact code."
            )

        # NO_MATCH. candidates is often EMPTY here — "AFS-JS" is not a substring
        # of "AFS - JS", so the icontains prefilter has nothing to offer. Naming
        # the catalogue field is the only useful thing left to say.
        hint = (f"Prefilter candidates: {resolution.candidates}"
                if resolution.candidates
                else "No similar codes found — check Event.event_code in the "
                     "event catalogue for the exact spelling.")
        raise serializers.ValidationError(
            f"No event with code '{code}' exists in the event catalogue. {hint}"
        )

    def validate_linkedin_followers(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("Follower count cannot be negative.")
        return value

    # NOTE: this class previously ended with a second `validate()` that called
    # `PaperReviewSerializer._validate_mr(self, attrs)` alongside
    # `_validate_mr = validate`. At class-body execution the name `validate` was
    # already bound to that SECOND definition, so the alias pointed at it and
    # every create/update recursed until RecursionError. Removed rather than
    # rewired: the criteria bounds it was reserving space for are already enforced
    # by the model's Min/MaxValueValidators, which ModelSerializer copies onto the
    # generated fields — there is no second stage to host. The MR check above is
    # now the one and only validate().
