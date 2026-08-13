"""
paper_review/views.py
──────────────────────
CRUD for the paper review form, the two Zoho workflows that fire on ADD, and the
shared CRM machinery (import, mass update, compound filters, export) so this
module behaves like Proposal Submission and Ticket Central rather than being a
one-off.

  PART A  Paper_to_Proposal_Submiss  — one ProposalSubmission per new review,
          in the SAME transaction (see proposal_bridge.py)
  PART B  Email_to_Production_Team   — the production-team notification, handed
          to transaction.on_commit so it can never break the create
          (see notifications.py)

Access is gated by crm_permission("paper_review") — registered in
accounts/models.py: CRM_MODULES and backfilled all-False by migration 0020, so the
module stays invisible until a role is granted it.

ROW SCOPE lives in get_queryset and comes from paper_review/access.py — exact set
membership on the caller's assigned event codes. RBACMixin.rbac_filter is
deliberately NOT used; access.py documents why.

BOTH WORKFLOWS ARE ADD-ONLY, matching `record event = on add` in Zoho. An edit
neither regenerates the proposal nor re-sends the email. That is a real
consequence: re-scoring a review leaves its proposal's qc_score stale, which is
why ProposalSubmission exposes qc_score_stale rather than letting the drift stay
invisible.

IMPORT FIRES NEITHER WORKFLOW — see import_commit. A 400-row historical import
must not send 400 emails and must not mint 400 proposal submissions, so it writes
PaperReview rows directly and never touches perform_create.
"""
import csv
import uuid

from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce, Lower
from django.http import StreamingHttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from accounts.audit import log_module_wipe
from accounts.bulk_update import BulkUpdateMixin, build_bulk_update_fields
from accounts.crm_permissions import crm_permission
from accounts.filter_spec import FilterSpecMixin, build_filter_spec_fields
from accounts.ordering import StableOrderingFilter
from accounts.permissions import IsHPAccount
from proposal_submission.models import ProposalSubmission

from .access import (
    has_full_visibility, may_see_mr_fields, may_use_event_code,
    permitted_event_codes, scope_queryset,
)
from .filters import PaperReviewFilter
from .importer import (
    CREATE, CREATE_WITH_WARNING, ERROR, FIELD_TO_LABEL, MAX_ROWS, MR_COLUMNS,
    AUDIT_COLUMNS, classify_rows, file_has_mr_content, map_headers, plan_hash,
    public_plan, summarise,
)
from accounts.import_common import catalogue_notice
from .models import CRITERIA, CRITERIA_FIELDS, RUBRIC_TOTAL, PaperReview
from .notifications import send_paper_review_notification
from .proposal_bridge import ProposalBridgeError, create_proposal_for_review
from .serializers import MR_ONLY_FIELDS, PaperReviewSerializer

# Query params that would let a user infer MR content they cannot read.
_MR_QUERY_PARAMS = tuple(MR_ONLY_FIELDS)

# Every business field, in model order. The contract test asserts it stays in step
# with the model.
BUSINESS_FIELDS = [
    "paper_submission_date", "event_code",
    "speaker_email_ref", "research_email_ref",
    "speaker_name", "company_name", "email",
    "linkedin_speaker", "linkedin_company", "linkedin_followers", "nos",
    *CRITERIA_FIELDS,
    "proposal_score", "grade", "session_location_on_agenda",
    "internal_footnotes", "feedback_to_speaker",
    "proposal_received", "theme", "agenda_addition",
]


class PaperReviewViewSet(FilterSpecMixin, BulkUpdateMixin, viewsets.ModelViewSet):
    """
    GET    /api/paper-reviews/            — list (paginated, filtered, searchable)
    POST   /api/paper-reviews/            — create (+ Part A proposal, + Part B email)
    GET    /api/paper-reviews/{id}/       — retrieve
    PATCH  /api/paper-reviews/{id}/       — partial update
    DELETE /api/paper-reviews/{id}/       — hard delete
    GET    /api/paper-reviews/export/     — streaming CSV
    GET    /api/paper-reviews/filter_options/
    GET    /api/paper-reviews/permitted_events/
    GET    /api/paper-reviews/filter_schema/
    GET    /api/paper-reviews/bulk_update_schema/
    POST   /api/paper-reviews/bulk_update/
    POST   /api/paper-reviews/import/preview/
    POST   /api/paper-reviews/import/commit/
    """
    permission_classes = [crm_permission("paper_review")]
    serializer_class   = PaperReviewSerializer
    filterset_class    = PaperReviewFilter

    # StableOrderingFilter appends the pk as a final tiebreaker to EVERY ordering:
    # paper_submission_date is non-unique AND nullable, so without it LIMIT/OFFSET
    # paging both repeats and silently SKIPS rows. Named explicitly so a future
    # DEFAULT_FILTER_BACKENDS change cannot quietly drop it.
    filter_backends = [DjangoFilterBackend, filters.SearchFilter,
                       StableOrderingFilter]

    search_fields = [
        "speaker_name", "company_name", "email", "event_code", "theme",
        "session_location_on_agenda",
    ]
    ordering_fields = [
        "id", "paper_submission_date", "speaker_name", "company_name", "email",
        "proposal_score", "grade", "linkedin_followers", "event_code",
        "created_at", "updated_at",
    ]
    ordering = ["-paper_submission_date"]

    # ── Mass update (C2) ──────────────────────────────────────────────────────
    # PaperReview has no parent FK, so every row is independent: no collateral,
    # no split-group UI, no blast-radius warning.
    #
    # Every editable column is wired EXCEPT:
    #   event_code, speaker_name, email, company_name — identity. Mass-setting
    #       them would overwrite per-person data with one value, and event_code
    #       is what the RBAC scope matches on.
    #   speaker_email_ref, research_email_ref — NOT user-entered. They cache the
    #       notification recipients resolved from the event, are read-only in the
    #       serializer and absent from the form (models.py:66-69).
    #   proposal_score — COMPUTED. save() recomputes it from the six criteria on
    #       every write, so a bulk write would be overwritten in the same
    #       statement and read as a silent no-op. Bulk-updating any CRITERION
    #       moves the score instead, which is the point — see
    #       get_bulk_update_side_effects below.
    #   import_batch_id, created_by, updated_by, created_at, updated_at, id —
    #       DEFAULT_EXCLUDES in accounts/bulk_update.py.
    #
    # The six criteria carry their rubric maxima automatically: each is a
    # PositiveSmallIntegerField with MaxValueValidator(<max>), which the builder
    # reads into "max" (and 0 into "min"). CRITERIA stays the single source of
    # truth — it is what defines those validators in the first place.
    bulk_update_fields = build_bulk_update_fields(
        PaperReview,
        exclude=(
            "event_code", "speaker_name", "email", "company_name",
            "speaker_email_ref", "research_email_ref",
            "proposal_score",
        ),
        # grade has no choices= at the DB level. The real vocabulary is not one
        # letter per row: the Zoho export carries A, B, B+, C, D and E, with 'B+'
        # the third most common at 355 of 3492 rows (models.py:106-115). Offering
        # only A-D here would refuse a value the column legitimately holds and
        # that the importer writes. Kept identical to qc_grade's list in
        # proposal_submission, which proposal_bridge copies this column into.
        choices={"grade": ["A", "B", "B+", "C", "D", "E"]},
        # The importer's column names, reused verbatim: a field must not be
        # called one thing in the import wizard, the CSV header and the export,
        # and something else in the mass-update picker. It already carries the
        # rubric maxima in the criterion labels ("Closeness to Topic (10)").
        labels=FIELD_TO_LABEL,
    )
    bulk_update_parent_path = None          # no parent — row writes only
    bulk_update_label       = "paper reviews"
    # Every criterion moves the computed total, and the preview must say so rather
    # than presenting a criterion edit as touching one column.
    bulk_update_side_effects = {}

    def get_bulk_update_side_effects(self, field, raw_value):
        """
        The six criteria feed a COMPUTED column. The shared mixin's static
        (field, value) table cannot express "any value of this field has this
        consequence", so it is expressed here instead.
        """
        if field in CRITERIA_FIELDS:
            return [
                f"Proposal Score is recomputed from all six criteria on every "
                f"affected row (out of {RUBRIC_TOTAL})."
            ]
        return super().get_bulk_update_side_effects(field, raw_value)

    # The "integer" type, its min/max bounds and the 400-not-500 guarantee for a
    # non-numeric value now live in accounts/bulk_update.py._coerce_number. This
    # module carried a private copy, as did proposal_submission; a fix to either
    # had to be found in both.

    # ── Compound filter engine ────────────────────────────────────────────────
    filter_spec_fields = build_filter_spec_fields(
        PaperReview,
        # Provenance and audit columns: import_batch_id has its own purpose-built
        # filter in filters.py, and filtering on who last touched a row is not a
        # use case anyone has asked for.
        exclude=("created_by", "updated_by", "import_batch_id"),
        labels={
            "event_code": "Event Code",
            "paper_submission_date": "Paper Submission Date",
            "email": "Email Address of the Speaker",
            "linkedin_speaker": "LinkedIn — Speaker",
            "linkedin_company": "LinkedIn — Company",
            "linkedin_followers": "LinkedIn Followers",
            "nos": "NOS?",
            "proposal_score": "Proposal Score",
            "session_location_on_agenda": "Session or Location on Agenda",
            "internal_footnotes": "Internal Footnotes",
            "feedback_to_speaker": "Feedback to Speaker",
            "speaker_email_ref": "Speaker Email Ref",
            "research_email_ref": "Research Email Ref",
            **{f: FIELD_TO_LABEL[f] for f in CRITERIA_FIELDS},
        },
    )

    def get_filter_spec_fields(self):
        """
        Strip the MR columns from the advertised registry for users who cannot
        read them, and feed the two choice-less dropdowns from stored values.
        """
        fields = dict(super().get_filter_spec_fields())
        if not may_see_mr_fields(getattr(self.request, "user", None)):
            for field in _MR_QUERY_PARAMS:
                fields.pop(field, None)
        for field, values in self._distinct_option_values().items():
            if field in fields and values:
                fields[field] = {**fields[field], "choices": values}
        return fields

    def get_queryset(self):
        """
        The single scope gate — list, retrieve, update, destroy, bulk_update and
        export all inherit it, so a new action cannot forget it. select_related on
        the audit FKs: the serializer renders both display names, which would
        otherwise be two extra queries per row.
        """
        qs = PaperReview.objects.select_related("created_by", "updated_by")
        qs = scope_queryset(qs, self.request.user)
        return self._annotate_duplicates(qs)

    def _annotate_duplicates(self, qs):
        """
        C1. duplicate_count = other rows sharing (lower(email), event_code).

        A Subquery annotation, not a SerializerMethodField doing a lookup: a
        per-row query would be 50 extra queries on every page. Subquery rather
        than a window function because a window annotation cannot be filtered, and
        the has_duplicates filter needs to.

        Built on the SCOPED queryset, so the count is what this user can actually
        see. A duplicate sitting outside their events therefore reads as "none" —
        surfaced in the UI copy rather than left implicit.
        """
        peers = (
            scope_queryset(PaperReview.objects.all(), self.request.user)
            .annotate(_peer_email=Lower("email"))
            .filter(_peer_email=Lower(OuterRef("email")),
                    event_code=OuterRef("event_code"))
            .exclude(pk=OuterRef("pk"))
            .order_by()
            .values("event_code")
            .annotate(n=Count("pk"))
            .values("n")[:1]
        )
        return qs.annotate(
            duplicate_count=Coalesce(
                Subquery(peers, output_field=IntegerField()), Value(0)
            )
        )

    # ── MR-internal field query guard ─────────────────────────────────────────

    def _reject_mr_query_params(self, request):
        """
        A user who cannot READ internal_footnotes must not be able to filter or
        order by it either: a row order under ?ordering=internal_footnotes leaks
        exactly what to_representation strips. Refused with 400 rather than
        ignored, so the caller is never misled about what their query did. Same
        rule as proposal_submission/views.py.
        """
        if may_see_mr_fields(request.user):
            return

        offending = set()
        for param in request.query_params:
            if param.split("__", 1)[0] in _MR_QUERY_PARAMS:
                offending.add(param)
        ordering = request.query_params.get("ordering", "")
        for term in (t.strip() for t in ordering.split(",") if t.strip()):
            if term.lstrip("-") in _MR_QUERY_PARAMS:
                offending.add(f"ordering={term}")
        spec = request.query_params.get("filter_spec", "")
        for field in _MR_QUERY_PARAMS:
            if field in spec:
                offending.add(f"filter_spec:{field}")

        if offending:
            raise ValidationError({
                "detail": "These fields are restricted to Market Research and "
                          "Admin and cannot be filtered or sorted on: "
                          + ", ".join(sorted(offending))
            })

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self._reject_mr_query_params(request)

    # ── Non-blocking duplicate warning (C1) ───────────────────────────────────

    def _duplicate_peer_count(self, obj):
        """Other rows sharing (email, event_code) within the caller's scope."""
        if obj is None:
            return 0
        return (
            scope_queryset(PaperReview.objects.all(), self.request.user)
            .filter(email__iexact=obj.email, event_code=obj.event_code)
            .exclude(pk=obj.pk).count()
        )

    def _attach_duplicate_warning(self, response, obj):
        """
        Advisory only. A resubmission is legitimate — the same speaker really does
        submit a paper for the same event twice — so this is neither a validation
        error nor a unique constraint, and the status stays 201.
        """
        peers = self._duplicate_peer_count(obj)
        if peers:
            response.data["duplicate_count"] = peers
            response.data["warning"] = (
                f"{peers} other paper review{'s' if peers != 1 else ''} from "
                f"{obj.email} for {obj.event_code} already "
                f"{'exist' if peers != 1 else 'exists'} in your events."
            )
        return response

    # ── Create: the review, its proposal, one log entry, one email ────────────

    def perform_create(self, serializer):
        """
        A5. ATOMIC — the review and its proposal are one write or neither.

        If the proposal cannot be created the ValidationError propagates out of
        the atomic block, the review is rolled back with it, and the 400 names the
        reason. Per-object save() throughout; never bulk_create.

        The Part B send is registered with transaction.on_commit INSIDE the block,
        so a rollback discards the callback and no email goes out for a review that
        does not exist (B6, and the other half of A5).
        """
        from accounts.models import ActionLog

        with transaction.atomic():
            review = serializer.save(created_by=self.request.user)

            try:
                proposal, created, peers = create_proposal_for_review(
                    review, self.request)
            except ProposalBridgeError as exc:
                detail = {"detail": exc.message}
                if exc.errors:
                    detail["proposal_submission"] = exc.errors
                raise ValidationError(detail) from exc

            # A10. ONE entry, naming both ids.
            ActionLog.objects.create(
                user=self.request.user,
                action=f"Created paper review #{review.id} → proposal "
                       f"submission #{proposal.id}",
                details=f"Speaker: {review.speaker_name}, "
                        f"Event: {review.event_code}, "
                        f"Score: {review.proposal_score}/{RUBRIC_TOTAL}, "
                        f"Grade: {review.grade or '—'}, "
                        f"Proposal: {'created' if created else 'already existed'}",
            )

            transaction.on_commit(
                lambda: send_paper_review_notification(review)
            )

        # Read by create() to build the response envelope.
        self._proposal_result = (proposal, created, peers)

    def create(self, request, *args, **kwargs):
        """
        201 body = the review, plus what the two workflows did.

        The notification is reported as `queued` rather than as a result: it runs
        on commit, which is after this response body is assembled, so claiming a
        delivery outcome here would be a guess. NotificationLog carries the
        outcome.
        """
        self._proposal_result = None
        response = super().create(request, *args, **kwargs)
        if response.status_code != status.HTTP_201_CREATED:
            return response

        review = PaperReview.objects.filter(pk=response.data.get("id")).first()
        self._attach_duplicate_warning(response, review)

        result = getattr(self, "_proposal_result", None)
        if result:
            proposal, created, peers = result
            block = {
                "id": proposal.id,
                "created": created,
                "duplicate_count": peers,
            }
            if not created:
                # A7 — surfaced, never silent.
                block["notice"] = (
                    "This paper review already had a proposal submission; a "
                    "second one was not created."
                )
            if peers:
                block["warning"] = (
                    f"{peers} other proposal{'s' if peers != 1 else ''} from "
                    f"{proposal.email} for {proposal.event_code} already "
                    f"{'exist' if peers != 1 else 'exists'} in your events."
                )
            response.data["proposal_submission"] = block

        response.data["notification"] = {
            "queued": True,
            "detail": "The production-team email is sent after this record "
                      "commits; see the notification log for the outcome.",
        }
        return response

    # ── Audit on the paths that do NOT trigger either workflow ────────────────

    def perform_update(self, serializer):
        from accounts.models import ActionLog
        with transaction.atomic():
            review = serializer.save(updated_by=self.request.user)
            ActionLog.objects.create(
                user=self.request.user,
                action=f"Edited paper review #{review.id}",
                details=f"Fields: {list(serializer.validated_data.keys())}"[:200],
            )

    def perform_destroy(self, instance):
        from accounts.models import ActionLog
        with transaction.atomic():
            ActionLog.objects.create(
                user=self.request.user,
                action=f"DELETED paper review #{instance.id}",
                details=f"Speaker: {instance.speaker_name}, "
                        f"Event: {instance.event_code}, "
                        f"Score was: {instance.proposal_score}, "
                        f"Grade was: {instance.grade or '—'}",
            )
            instance.delete()

    # ── Distinct stored values for the filter dropdowns (C4) ──────────────────
    #
    # ISOLATED IN ONE FUNCTION ON PURPOSE. Neither field has choices= at the model
    # level: grade is manual and its bands are inferred rather than confirmed, and
    # session_location_on_agenda is very likely per-event in reality (a slot name
    # from one event's agenda means nothing on another's). Until either is
    # confirmed, the filter dropdowns must offer what the data actually contains —
    # a placeholder nobody has used returns count=0 and reads as a bug. The moment
    # real choices= land on the model, this function and its two callers can be
    # deleted outright.
    OPTION_FIELDS = ("grade", "session_location_on_agenda")

    def _distinct_option_values(self):
        """
        {field: [distinct non-empty stored values, sorted]}, RBAC-scoped and
        cached for the life of the request — get_filter_spec_fields and the
        filter_options endpoint both call it.
        """
        cached = getattr(self, "_option_cache", None)
        if cached is not None:
            return cached
        qs = scope_queryset(PaperReview.objects.all(), self.request.user)
        out = {}
        for field in self.OPTION_FIELDS:
            values = (
                qs.exclude(**{field: ""}).exclude(**{f"{field}__isnull": True})
                .order_by(field).values_list(field, flat=True).distinct()
            )
            out[field] = sorted(set(values))
        self._option_cache = out
        return out

    @action(detail=False, methods=["get"], url_path="filter_options")
    def filter_options(self, request):
        """
        GET /api/paper-reviews/filter_options/

        Distinct stored values per dropdown field, scoped to the caller. Feeds the
        FILTER dropdowns only — the create/edit form keeps the placeholder lists,
        because a value nobody has used yet must still be selectable there.
        """
        return Response(self._distinct_option_values())

    @action(detail=False, methods=["get"], url_path="permitted_events")
    def permitted_events(self, request):
        """
        GET /api/paper-reviews/permitted_events/

        The event codes this user may actually attach a review to, straight from
        access.py. The form's event picker reads this instead of the whole
        catalogue: otherwise a scoped user is offered all 142 events and gets a
        400 on save, which reads as a broken module rather than a scoped one.

        Served from THIS module rather than events/?assigned_only=true because the
        two rules genuinely differ — EventViewSet.get_queryset also grants on
        sales_executive and gates only on is_admin, so an is_all_access non-admin
        would be shown FEWER events than they may use. Single-sourcing from
        access.py makes picker and validator incapable of disagreeing.
        """
        from events.models import Event
        if has_full_visibility(request.user):
            events = Event.objects.all()
        else:
            events = Event.objects.filter(
                event_code__in=permitted_event_codes(request.user))
        rows = list(
            events.order_by("event_code").values("event_code", "name", "event_date")
        )
        return Response({"unrestricted": has_full_visibility(request.user),
                         "count": len(rows), "results": rows})

    # ── CSV export (C3) ───────────────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request):
        """
        GET /api/paper-reviews/export/ → streaming CSV.

        Respects, in order: RBAC scope and the duplicate annotation
        (get_queryset), the active filters/search/filter_spec and the current
        ordering (filter_queryset — the same pipeline the list view uses), and MR
        stripping for users who cannot read internal_footnotes. An export that
        skipped any of those would be a data leak with a filename attached.

        Headers are Zoho display labels so an export round-trips through the
        importer unchanged. csv.writer quotes them where needed, which matters
        here more than in proposal_submission: two paper-review labels contain
        commas or apostrophes ("Case Study, Results, Examples (5)", "Not an
        obvious 'Sales Pitch' (5)") and an unquoted comma would split one column
        into three on re-import.
        """
        queryset = self.filter_queryset(self.get_queryset())

        fields = [f for f in FIELD_TO_LABEL]
        if not may_see_mr_fields(request.user):
            fields = [f for f in fields if f not in _MR_QUERY_PARAMS]
        header = [FIELD_TO_LABEL[f] for f in fields]

        class _Echo:
            """A file-like object that returns the line instead of storing it."""
            def write(self, value):
                return value

        writer = csv.writer(_Echo())

        def rows():
            yield writer.writerow(header)
            # .iterator() so a large export is never materialised in memory.
            for obj in queryset.iterator(chunk_size=500):
                yield writer.writerow([
                    "" if getattr(obj, f) is None else getattr(obj, f)
                    for f in fields
                ])

        response = StreamingHttpResponse(rows(), content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="paper-reviews.csv"'
        )
        return response

    # ── Import: preview / commit (Part B) ─────────────────────────────────────

    def _build_plan(self, request):
        """
        Shared by preview and commit so the two cannot drift. Returns
        (plan, unrecognised_columns, ignored_columns) or raises ValidationError.
        """
        rows = request.data.get("rows")
        if not isinstance(rows, list):
            raise ValidationError({"rows": "Expected a list of row objects."})
        if not rows:
            raise ValidationError({"rows": "No rows supplied."})
        if len(rows) > MAX_ROWS:
            raise ValidationError({
                "rows": f"{len(rows)} rows supplied; the maximum per call is "
                        f"{MAX_ROWS}. Split the file into chunks of "
                        f"{MAX_ROWS} rows or fewer."
            })
        if not all(isinstance(r, dict) for r in rows):
            raise ValidationError({"rows": "Every row must be an object."})

        columns = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        mapping, unrecognised = map_headers(columns)
        if not mapping:
            raise ValidationError({
                "rows": "No recognisable columns. Expected Zoho labels such as "
                        "'Event Code', 'Speaker Name', "
                        "'Email Address of the Speaker'."
            })

        # B7 — whole-file refusal, not per-row dropping. Silently discarding MR
        # content would let the importer believe those notes landed.
        if not may_see_mr_fields(request.user):
            offending = file_has_mr_content(rows, mapping)
            if offending:
                raise ValidationError({
                    "detail": "This file contains Market Research content in "
                              + ", ".join(offending)
                              + ". Those columns are restricted to Market "
                                "Research and Admin — remove them or ask MR to "
                                "run the import.",
                    "columns": offending,
                })

        # Recognised but deliberately not written — reported so "Added User" does
        # not look like it was imported. See importer.AUDIT_COLUMNS.
        ignored = sorted({
            FIELD_TO_LABEL.get(field, column)
            for column, field in mapping.items() if field in AUDIT_COLUMNS
        } | {
            column for column, field in mapping.items()
            if field in AUDIT_COLUMNS and field not in FIELD_TO_LABEL
        })

        scoped = scope_queryset(PaperReview.objects.all(), request.user)
        existing_pairs = {
            (email.lower(), code) for email, code in
            scoped.values_list("email", "event_code")
        }
        plan = classify_rows(rows, mapping, request.user, existing_pairs)
        return plan, unrecognised, ignored

    def _resolve_import_batch_id(self, request):
        """
        The first preview call for a file mints a fresh UUID and returns it; every
        later preview/commit call for the SAME file passes it back, and this
        validates and echoes it — so all chunks converge on one id without the
        backend needing to know what "one file" means across independent HTTP
        calls. Mirrors ProposalSubmissionViewSet exactly.
        """
        raw = request.data.get("import_batch_id")
        if raw in (None, ""):
            return uuid.uuid4()
        try:
            return uuid.UUID(str(raw))
        except (ValueError, AttributeError, TypeError):
            raise ValidationError({
                "import_batch_id": f"{raw!r} is not a valid UUID."})

    @action(detail=False, methods=["post"], url_path="import/preview")
    def import_preview(self, request):
        """POST /api/paper-reviews/import/preview/ — writes NOTHING."""
        plan, unrecognised, ignored = self._build_plan(request)
        counts = summarise(plan)
        batch_id = self._resolve_import_batch_id(request)
        return Response({
            "success": True,
            "plan_hash": plan_hash(plan),
            "import_batch_id": str(batch_id),
            "counts": counts,
            "importable": counts[CREATE] + counts[CREATE_WITH_WARNING],
            "unrecognised_columns": unrecognised,
            "ignored_columns": ignored,
            # Why NOTHING in the file can import, when the reason is the system
            # rather than the rows. See accounts/import_common.catalogue_notice.
            "notice": catalogue_notice(),
            # B2 — stated in the preview, so nobody has to infer it from
            # behaviour after the fact.
            "workflows_suppressed": {
                "proposal_submission": True,
                "production_team_email": True,
                "detail": "An import creates paper reviews ONLY. It does not "
                          "generate proposal submissions and does not send "
                          "production-team notifications — those fire on form "
                          "creates only.",
            },
            "rows": public_plan(plan),
        })

    @action(detail=False, methods=["post"], url_path="import/commit")
    def import_commit(self, request):
        """
        POST /api/paper-reviews/import/commit/ — requires plan_hash and
        import_batch_id.

        ERROR rows are skipped; everything else is written. Per-object save()
        inside one transaction.atomic() — never queryset.update(), never
        bulk_create, so PaperReview.save()'s proposal_score recomputation runs for
        every row and a failure anywhere rolls the whole batch back.

        B2 — THE HIGHEST-CONSEQUENCE PROPERTY IN THIS MODULE. This writes
        PaperReview rows DIRECTLY and never calls perform_create, so neither
        workflow fires: no ProposalSubmission per row, and no
        transaction.on_commit notification. A 400-row historical import must not
        send 400 emails or mint 400 proposals. Structural, not incidental — there
        is no call path from here to either one, and it does not depend on
        PAPER_REVIEW_NOTIFICATIONS_ENABLED being False.
        """
        from accounts.models import ActionLog

        client_hash = request.data.get("plan_hash")
        if not client_hash:
            raise ValidationError({"plan_hash": "Required. Call import/preview/ first."})
        if not request.data.get("import_batch_id"):
            raise ValidationError({
                "import_batch_id": "Required. Use the value returned by "
                                   "import/preview/."})
        batch_id = self._resolve_import_batch_id(request)

        plan, unrecognised, ignored = self._build_plan(request)
        fresh_hash = plan_hash(plan)
        counts = summarise(plan)

        if client_hash != fresh_hash:
            # Nothing is written on a stale hash — never a partial import.
            return Response({
                "success": False,
                "detail": "The data changed since this preview was generated. "
                          "Review the refreshed plan and confirm again.",
                "plan_hash": fresh_hash,
                "import_batch_id": str(batch_id),
                "counts": counts,
                "unrecognised_columns": unrecognised,
                "ignored_columns": ignored,
                "rows": public_plan(plan),
            }, status=status.HTTP_409_CONFLICT)

        importable = [e for e in plan if e["classification"] != ERROR]
        created_ids = []
        with transaction.atomic():
            for entry in importable:
                obj = PaperReview(created_by=request.user,
                                  import_batch_id=batch_id,
                                  **entry["_payload"])
                obj.save()      # recomputes proposal_score from the criteria
                created_ids.append(obj.id)

            filename = str(request.data.get("filename") or "(unnamed file)")
            ActionLog.objects.create(
                user=request.user,
                action=f"Imported {len(created_ids)} paper reviews "
                       f"({counts[CREATE]} new, "
                       f"{counts[CREATE_WITH_WARNING]} warned, "
                       f"{counts[ERROR]} skipped)",
                # details is a TextField and carries the COMPLETE id list —
                # bulk_delete's ids[:50] truncation loses the audit trail.
                details=f"file={filename}\n"
                        f"import_batch_id={batch_id}\n"
                        f"created_ids={created_ids}\n"
                        f"unrecognised_columns={unrecognised}\n"
                        f"ignored_columns={ignored}\n"
                        f"workflows=suppressed (no proposal submissions, no emails)",
            )

        return Response({
            "success": True,
            "created": len(created_ids),
            "created_ids": created_ids,
            "import_batch_id": str(batch_id),
            "skipped": counts[ERROR],
            "counts": counts,
            "unrecognised_columns": unrecognised,
            "ignored_columns": ignored,
            "workflows_suppressed": {
                "proposal_submission": True,
                "production_team_email": True,
            },
            "rows": public_plan(plan),
        }, status=status.HTTP_201_CREATED)

    # ── Scope enforcement on the inherited bulk action ────────────────────────

    @action(detail=False, methods=["post"], url_path="bulk_update")
    def bulk_update(self, request, *args, **kwargs):
        """
        Same contract as the shared mixin, with two additions.

        1. An id the caller cannot see is a 404, not a quiet no-op. The mixin
           already scopes correctly — it intersects the submitted ids with
           get_queryset() — but an entirely out-of-scope batch comes back 200 with
           `permitted: 0`, which reads as "your edit applied to 0 rows" rather
           than "that row is not yours".
        2. internal_footnotes is refused for users who cannot see it. Without
           this, a non-MR user could mass-write a column that is stripped from
           their own reads — writing content they can never verify, over content
           they were never shown.
        """
        field = request.data.get("field")
        if field in _MR_QUERY_PARAMS and not may_see_mr_fields(request.user):
            raise ValidationError({
                "field": f"'{field}' is restricted to Market Research and Admin."
            })

        ids = request.data.get("ids")
        if isinstance(ids, list) and ids and all(isinstance(i, int) for i in ids):
            visible = set(
                self.get_queryset().filter(id__in=ids).values_list("id", flat=True)
            )
            unseen = [i for i in ids if i not in visible]
            if unseen:
                raise NotFound(
                    f"No paper review matching id(s) {sorted(unseen)}."
                )
        # Non-int / malformed ids fall through to the mixin's own 400.
        return super().bulk_update(request)

    @action(detail=False, methods=["get"], url_path="bulk_update_schema")
    def bulk_update_schema(self, request):
        """
        Hide the MR field from the advertised schema for users who cannot read it —
        offering a checkbox for a column whose values are stripped from their own
        reads invites exactly the blind mass-write the guard in bulk_update()
        refuses. Re-decorated with @action so the route survives the override.
        """
        response = super().bulk_update_schema(request)
        if not may_see_mr_fields(request.user):
            fields = dict(response.data.get("fields") or {})
            for name in _MR_QUERY_PARAMS:
                fields.pop(name, None)
            response.data["fields"] = fields
        return response

    @action(detail=False, methods=["delete"], url_path="clear_all",
            permission_classes=[IsHPAccount])
    def clear_all(self, request):
        """
        DELETE /api/paper-reviews/clear_all/ — HP only (accounts.permissions.IsHPAccount).

        NOT scoped by get_queryset(). Every other read and write in this viewset is
        narrowed to the caller's event codes by scope_queryset(); this one is
        deliberately the whole table, because "clear all data" that silently left
        another team's reviews behind would report success having done half the job.
        The gate is that only one account can call it at all.

        WHAT SURVIVES, AND WHY THAT IS CORRECT
        ProposalSubmission.source_paper_review is on_delete=SET_NULL, so the proposals
        this module generated (Part A, proposal_bridge.py) are UNLINKED rather than
        destroyed — they belong to Proposal Submission, which has its own wipe. Its
        `qc_score_stale` flag already models a proposal whose review has moved on, so
        an unlinked proposal is a state that module understands.

        NotificationLog rows ARE deleted: they are this module's own record of the
        Part B emails, and keeping send history for reviews that no longer exist just
        leaves rows nothing can be traced back to.
        """
        from django.db import transaction

        from .models import NotificationLog

        with transaction.atomic():
            deleted = {
                "paper_reviews": PaperReview.objects.count(),
                "notification_logs": NotificationLog.objects.count(),
            }
            unlinked = ProposalSubmission.objects.filter(
                source_paper_review__isnull=False).count()
            NotificationLog.objects.all().delete()
            PaperReview.objects.all().delete()
            log_module_wipe(request.user, "PAPER REVIEW", deleted)
        return Response({
            "detail": "Successfully removed all paper review data.",
            "deleted": deleted,
            # Reported rather than left for someone to discover: these proposals still
            # exist, they just no longer point at a review.
            "proposals_unlinked": unlinked,
        })
