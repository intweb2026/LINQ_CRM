"""
proposal_submission/views.py
─────────────────────────────
CRUD plus the shared CRM machinery: mass update, the compound filter engine and
stable ordering, so this module behaves like Bookings and Ticket Central rather
than being a one-off.

Access is gated by crm_permission("proposal_submission") — the module is already
registered in accounts/models.py: CRM_MODULES and backfilled all-False by
migration 0020, so the feature stays invisible until a role is granted it.

ROW SCOPE lives in get_queryset and comes from proposal_submission/access.py —
exact set membership on the caller's assigned event codes. RBACMixin.rbac_filter
is deliberately NOT used: it matches with event_code__icontains, so a user
assigned "BIU" would also receive every "BIUK - PM" row, and its second branch
grants on a sales_executive column this model does not have. See access.py for
the full reasoning.
"""
import csv
import logging
import uuid

from django.db import transaction
from django.db.models import (
    BooleanField, Case, Count, F, IntegerField, OuterRef, Q, Subquery, Value, When,
)
from django.db.models.functions import Coalesce, Lower
from django.http import StreamingHttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from accounts.audit import log_module_wipe
from accounts.bulk_update import BulkUpdateMixin
from accounts.crm_permissions import crm_permission
from accounts.filter_spec import FilterSpecMixin, build_filter_spec_fields
from accounts.ordering import StableOrderingFilter
from accounts.permissions import IsHPAccount
from .access import (
    has_full_visibility, may_see_mr_fields, may_use_event_code,
    permitted_event_codes, scope_queryset,
)
from .filters import ProposalSubmissionFilter
from .importer import (
    CREATE, CREATE_WITH_WARNING, ERROR, FIELD_TO_LABEL, MAX_ROWS, MR_COLUMNS,
    classify_rows, file_has_mr_content, map_headers, plan_hash, public_plan,
    summarise,
)
from .models import ProposalSubmission
from .serializers import MR_ONLY_FIELDS, ProposalSubmissionSerializer

logger = logging.getLogger(__name__)

# Filter/order params that would let a user infer MR content they cannot read.
# Kept as a tuple of the underlying field names plus every filter alias that
# targets them.
_MR_QUERY_PARAMS = tuple(MR_COLUMNS)

# Every business field, in model order. The duplicate action copies exactly this
# set, and the contract test asserts it stays in step with the model.
BUSINESS_FIELDS = [
    "event_code", "submission_date", "participation_type",
    "speaker_name", "email", "company_name",
    "qc_grade", "qc_score", "sales_pitch_factor", "presentation_theme",
    "linkedin_speaker", "linkedin_company", "linkedin_followers",
    "speaker_slot_status", "sponsorship_status", "spex_remarks",
    "agenda_slot", "revenue_possibility",
    "internal_footnotes_mr", "slot_recommendation_mr", "agenda_addition",
]


class ProposalSubmissionViewSet(FilterSpecMixin, BulkUpdateMixin,
                                viewsets.ModelViewSet):
    """
    GET    /api/proposal-submissions/            — list (paginated, filtered, searchable)
    POST   /api/proposal-submissions/            — create
    GET    /api/proposal-submissions/{id}/       — retrieve
    PATCH  /api/proposal-submissions/{id}/       — partial update
    DELETE /api/proposal-submissions/{id}/       — hard delete
    POST   /api/proposal-submissions/{id}/duplicate/ — copy to a new row
    GET    /api/proposal-submissions/filter_schema/
    GET    /api/proposal-submissions/bulk_update_schema/
    POST   /api/proposal-submissions/bulk_update/
    """
    permission_classes = [crm_permission("proposal_submission")]
    serializer_class   = ProposalSubmissionSerializer
    filterset_class    = ProposalSubmissionFilter

    # Declared explicitly rather than inherited from DEFAULT_FILTER_BACKENDS.
    # StableOrderingFilter appends the pk as a final tiebreaker to EVERY
    # ordering, default or user-selected; submission_date is non-unique AND
    # nullable, so without it LIMIT/OFFSET paging both repeats and silently
    # SKIPS rows. Naming the backends here means a future settings change cannot
    # quietly drop it from this endpoint.
    filter_backends = [DjangoFilterBackend, filters.SearchFilter,
                       StableOrderingFilter]

    search_fields = [
        "speaker_name", "email", "company_name", "event_code",
        "presentation_theme", "agenda_slot", "spex_remarks",
    ]
    ordering_fields = [
        "id", "submission_date", "speaker_name", "company_name", "email",
        "qc_score", "qc_grade", "participation_type",
        "speaker_slot_status", "sponsorship_status", "revenue_possibility",
        "linkedin_followers", "created_at", "updated_at",
    ]
    ordering = ["-submission_date"]

    # ── Mass update ───────────────────────────────────────────────────────────
    # Whitelist only. ProposalSubmission has no parent FK, so every row is
    # independent: no collateral, no split-group UI, no blast-radius warning.
    #
    # Identity fields (event_code, speaker_name, email, company_name) and the
    # LinkedIn URLs are deliberately absent — mass-setting them would overwrite
    # per-person data with one value, which is never a legitimate bulk action.
    #
    # The choice lists mirror the frontend placeholders. Because the model has no
    # choices= at the DB level, this allow-list is the ONLY value safety these
    # fields have — nothing at the database or model layer will catch a bad value.
    bulk_update_fields = {
        "qc_grade": {
            "group": "row", "type": "choice", "label": "QC Grade",
            "choices": ["A", "B", "C", "D"],
        },
        "qc_score": {
            "group": "row", "type": "integer", "label": "QC Score", "nullable": True,
        },
        "speaker_slot_status": {
            "group": "row", "type": "choice", "label": "Speaker Slot Status",
            "choices": ["Pending", "Confirmed", "Declined", "Waitlisted"],
        },
        "sponsorship_status": {
            "group": "row", "type": "choice", "label": "Sponsorship Status",
            "choices": ["Pending", "Confirmed", "Declined", "Not Applicable"],
        },
        "agenda_slot": {
            "group": "row", "type": "text", "label": "Agenda Slot",
        },
        "revenue_possibility": {
            "group": "row", "type": "choice", "label": "Revenue Possibility",
            "choices": ["Low", "Medium", "High"],
        },
        "sales_pitch_factor": {
            "group": "row", "type": "text", "label": "Sales Pitch Factor",
        },
        "agenda_addition": {
            "group": "row", "type": "text", "label": "Agenda Addition",
        },
        "spex_remarks": {
            "group": "row", "type": "text", "label": "SPEX Remarks",
        },
    }
    bulk_update_parent_path = None          # no parent — row writes only
    bulk_update_label       = "proposal submissions"
    # No field here has a save()-derived side effect, so there is nothing for the
    # preview to understate.
    bulk_update_side_effects = {}

    # ── Compound filter engine ────────────────────────────────────────────────
    # Same registry mechanism as Bookings, so useFilterSpec, FilterBuilderModal
    # and FilterChips work here unchanged. Audit columns are excluded: filtering
    # on who last touched a row is not a use case anyone has asked for, and
    # created_at/updated_at are already dropped by DEFAULT_EXCLUDES.
    filter_spec_fields = build_filter_spec_fields(
        ProposalSubmission,
        # source_paper_review / import_batch_id are excluded for the same reason
        # as the audit columns: both are provenance, and each already has its own
        # purpose-built filter (import_batch_id via ProposalSubmissionFilter
        # above) rather than the raw id this generic registry would offer.
        exclude=("created_by", "updated_by", "source_paper_review",
                 "import_batch_id"),
        labels={
            "event_code": "Event Code",
            "qc_grade": "QC Grade",
            "qc_score": "QC Score",
            "linkedin_speaker": "LinkedIn — Speaker",
            "linkedin_company": "LinkedIn — Company",
            "linkedin_followers": "LinkedIn Followers",
            "spex_remarks": "SPEX Remarks",
            "internal_footnotes_mr": "Internal Footnotes (MR)",
            "slot_recommendation_mr": "Slot Recommendation (MR)",
        },
    )

    def _coerce(self, value, config):
        """
        Extend the shared coercer with an "integer" type.

        BulkUpdateMixin._coerce knows only choice / date / boolean / text.
        qc_score is an IntegerField, and declaring it "text" would pass a raw
        string through to obj.save(): "42" happens to work, but "abc" raises
        ValueError deep in the ORM and surfaces as an unhandled 500 instead of a
        400 naming the field. Overridden here rather than in accounts/bulk_update.py
        because three other modules share that file and none of them needs a
        numeric type yet.

        The modal renders unknown types as a plain text input, so "integer" needs
        no frontend change.
        """
        if config.get("type") == "integer":
            if value is None:
                if config.get("nullable"):
                    return None, None
                return None, "This field cannot be cleared."
            # bool is an int subclass in Python — reject it before int() accepts it.
            if isinstance(value, bool):
                return None, "Value must be a whole number."
            try:
                coerced = int(str(value).strip())
            except (TypeError, ValueError):
                return None, f"'{value}' is not a whole number."
            if coerced < 0:
                return None, "Value cannot be negative."
            return coerced, None
        return super()._coerce(value, config)

    def get_queryset(self):
        """
        The single scope gate. Everything else inherits from it:

          * list / filter_spec / search / pagination count
          * retrieve, update, partial_update, destroy — via get_object()
          * duplicate — via get_object()
          * bulk_update — the mixin resolves permitted ids from get_queryset()
            (accounts/bulk_update.py:234, "RBAC-scoped — never Model.objects")

        Scoping here rather than per-action means a new action cannot forget it.
        select_related on the audit FKs: the serializer renders both display
        names, which would otherwise be two extra queries per row.
        """
        qs = ProposalSubmission.objects.select_related("created_by", "updated_by")
        qs = scope_queryset(qs, self.request.user)
        return self._annotate_stale_qc_score(self._annotate_duplicates(qs))

    def _annotate_duplicates(self, qs):
        """
        duplicate_count = other rows sharing (lower(email), event_code).

        A Subquery annotation, not a SerializerMethodField: a per-row lookup would
        be 50 extra queries on every page. Subquery rather than a window function
        because a window annotation cannot be filtered, and B2's has_duplicates
        filter needs to.

        Built on the SCOPED queryset, so the count is what this user can actually
        see. A duplicate sitting outside their events therefore reads as "none" —
        surfaced in the UI copy rather than left implicit.
        """
        peers = (
            scope_queryset(ProposalSubmission.objects.all(), self.request.user)
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

    def _annotate_stale_qc_score(self, qs):
        """
        A3. qc_score_stale = this row came from a paper review AND its qc_score no
        longer matches that review's proposal_score.

        A JOIN through the nullable FK, not a per-row lookup: the flag has to be
        free on a 50-row page, and neither Paper Review workflow propagates edits
        (both are on-add), so drift is the expected state rather than an anomaly.

        THE NULL CASES ARE EXPLICIT. In SQL `NULL = NULL` is unknown, not true, so
        a naive equality test would report an unscored review with an unscored
        proposal as stale. Both-null is NOT drift; exactly-one-null is.
        """
        review_score = F("source_paper_review__proposal_score")
        return qs.annotate(
            qc_score_stale=Case(
                # No provenance — a manually created, imported or duplicated row.
                When(Q(source_paper_review__isnull=True), then=Value(False)),
                When(Q(qc_score__isnull=True)
                     & Q(source_paper_review__proposal_score__isnull=True),
                     then=Value(False)),
                When(Q(qc_score__isnull=False)
                     & Q(source_paper_review__proposal_score__isnull=False)
                     & Q(qc_score=review_score),
                     then=Value(False)),
                default=Value(True),
                output_field=BooleanField(),
            )
        )

    # ── MR-field query guards (G2) ────────────────────────────────────────────

    def _reject_mr_query_params(self, request):
        """
        A user who cannot READ the MR columns must not be able to filter or order
        by them either: a result count under `internal_footnotes_mr__icontains`,
        or a row order under `?ordering=slot_recommendation_mr`, leaks the content
        that to_representation strips. Refused with 400 rather than ignored, so
        the caller is never misled about what their query did.
        """
        if may_see_mr_fields(request.user):
            return

        offending = set()
        for param in request.query_params:
            base = param.split("__", 1)[0]
            if base in _MR_QUERY_PARAMS:
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

    def get_filter_spec_fields(self):
        """
        Strip the MR columns from the advertised registry for users who cannot
        read them, and feed the five choice-less dropdowns from stored values.
        """
        fields = dict(super().get_filter_spec_fields())
        if not may_see_mr_fields(getattr(self.request, "user", None)):
            for field in _MR_QUERY_PARAMS:
                fields.pop(field, None)
        for field, values in self._distinct_option_values().items():
            if field in fields and values:
                fields[field] = {**fields[field], "choices": values}
        return fields

    # ── Non-blocking duplicate warning (B3) ───────────────────────────────────

    def _duplicate_peer_count(self, obj):
        """Other rows sharing (email, event_code) within the caller's scope."""
        if obj is None:
            return 0
        return (
            scope_queryset(ProposalSubmission.objects.all(), self.request.user)
            .filter(email__iexact=obj.email, event_code=obj.event_code)
            .exclude(pk=obj.pk).count()
        )

    def _attach_duplicate_warning(self, response, obj):
        """
        Advisory only. A resubmission is legitimate — the same speaker really does
        pitch the same event twice — so this is neither a validation error nor a
        unique constraint, and the status stays 201.
        """
        peers = self._duplicate_peer_count(obj)
        if peers:
            response.data["duplicate_count"] = peers
            response.data["warning"] = (
                f"{peers} other proposal{'s' if peers != 1 else ''} from "
                f"{obj.email} for {obj.event_code} already "
                f"{'exist' if peers != 1 else 'exists'} in your events."
            )
        return response

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        if response.status_code == status.HTTP_201_CREATED:
            obj = ProposalSubmission.objects.filter(
                pk=response.data.get("id")).first()
            self._attach_duplicate_warning(response, obj)
        return response

    # ── Audit ─────────────────────────────────────────────────────────────────

    def perform_create(self, serializer):
        from accounts.models import ActionLog
        with transaction.atomic():
            proposal = serializer.save(created_by=self.request.user)
            ActionLog.objects.create(
                user=self.request.user,
                action=f"Created proposal submission #{proposal.id}",
                details=f"Speaker: {proposal.speaker_name}, "
                        f"Event: {proposal.event_code}, "
                        f"Type: {proposal.participation_type or '—'}",
            )

    def perform_update(self, serializer):
        from accounts.models import ActionLog
        with transaction.atomic():
            proposal = serializer.save(updated_by=self.request.user)
            ActionLog.objects.create(
                user=self.request.user,
                action=f"Edited proposal submission #{proposal.id}",
                details=f"Fields: {list(serializer.validated_data.keys())}"[:200],
            )

    def perform_destroy(self, instance):
        from accounts.models import ActionLog
        with transaction.atomic():
            ActionLog.objects.create(
                user=self.request.user,
                action=f"DELETED proposal submission #{instance.id}",
                details=f"Speaker: {instance.speaker_name}, "
                        f"Event: {instance.event_code}, "
                        f"Slot status was: {instance.speaker_slot_status or '—'}, "
                        f"Sponsorship status was: {instance.sponsorship_status or '—'}",
            )
            instance.delete()

    # ── Distinct stored values for the filter dropdowns (Part F) ──────────────
    #
    # ISOLATED IN ONE FUNCTION ON PURPOSE. The five fields have no choices= at the
    # model level because the real Zoho picklists are unconfirmed. Until they are,
    # the filter dropdowns must offer what the data actually contains — the
    # placeholder lists offered "Sponsor", which no row uses, so the filter
    # returned count=0 and read as a bug. The moment real choices= land on the
    # model, this whole function and its two callers can be deleted outright.
    OPTION_FIELDS = (
        "participation_type", "qc_grade", "speaker_slot_status",
        "sponsorship_status", "revenue_possibility",
    )

    def _distinct_option_values(self):
        """
        {field: [distinct non-empty stored values, sorted]}, RBAC-scoped and
        cached for the life of the request — get_filter_spec_fields and the
        filter_options endpoint both call it.
        """
        cached = getattr(self, "_option_cache", None)
        if cached is not None:
            return cached
        qs = scope_queryset(ProposalSubmission.objects.all(), self.request.user)
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
        GET /api/proposal-submissions/filter_options/

        Distinct stored values per dropdown field, scoped to the caller. Follows
        the shape of events/views.py:152 (`years`). Feeds the FILTER dropdowns
        only — the create/edit form keeps the placeholder lists, because a value
        nobody has used yet must still be selectable there.
        """
        return Response(self._distinct_option_values())

    @action(detail=False, methods=["get"], url_path="permitted_events")
    def permitted_events(self, request):
        """
        GET /api/proposal-submissions/permitted_events/

        The event codes this user may actually attach a proposal to, straight from
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

    # ── CSV export (Part E) ───────────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request):
        """
        GET /api/proposal-submissions/export/ → streaming CSV.

        Respects, in order: RBAC scope and duplicate annotation (get_queryset),
        the active filters/search/filter_spec and the current ordering
        (filter_queryset — the same pipeline the list view uses), and MR stripping
        for users who cannot read those columns. An export that skipped any of
        those would be a data leak with a filename attached.

        Headers are Zoho display labels so an export round-trips through the
        importer unchanged.
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
            'attachment; filename="proposal-submissions.csv"'
        )
        return response

    # ── Import: preview / commit (Part A) ─────────────────────────────────────

    def _build_plan(self, request):
        """
        Shared by preview and commit so the two cannot drift. Returns
        (plan, unrecognised_columns) or raises ValidationError.
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
                        "'Event Code', 'Speaker Name', 'Email Address'."
            })

        # A7 — whole-file refusal, not per-row dropping. Silently discarding MR
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

        scoped = scope_queryset(ProposalSubmission.objects.all(), request.user)
        existing_pairs = {
            (email.lower(), code) for email, code in
            scoped.values_list("email", "event_code")
        }
        plan = classify_rows(rows, mapping, request.user, existing_pairs)
        return plan, unrecognised

    def _resolve_import_batch_id(self, request):
        """
        C4. The first preview call for a file mints a fresh UUID and returns it;
        every later preview/commit call for the SAME file passes it back in the
        body, and this just validates and echoes it — so all chunks converge on
        one id without the backend needing to know what "one file" means across
        independent HTTP calls.
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
        """POST /api/proposal-submissions/import/preview/ — writes nothing."""
        plan, unrecognised = self._build_plan(request)
        counts = summarise(plan)
        batch_id = self._resolve_import_batch_id(request)
        return Response({
            "success": True,
            "plan_hash": plan_hash(plan),
            "import_batch_id": str(batch_id),
            "counts": counts,
            "importable": counts[CREATE] + counts[CREATE_WITH_WARNING],
            "unrecognised_columns": unrecognised,
            "rows": public_plan(plan),
        })

    @action(detail=False, methods=["post"], url_path="import/commit")
    def import_commit(self, request):
        """
        POST /api/proposal-submissions/import/commit/ — requires plan_hash.

        ERROR rows are skipped; everything else is written. Per-object save()
        inside one transaction.atomic() — never queryset.update(), never
        bulk_create, so model save() logic and auto_now both run and a failure
        anywhere rolls the whole batch back.
        """
        from accounts.models import ActionLog

        client_hash = request.data.get("plan_hash")
        if not client_hash:
            raise ValidationError({"plan_hash": "Required. Call import/preview/ first."})
        # C4 — required the same way plan_hash is: a commit that mints its own id
        # silently could not be joined to the preview (or to earlier chunks of the
        # same file) that a caller believes it shares an id with.
        raw_batch_id = request.data.get("import_batch_id")
        if not raw_batch_id:
            raise ValidationError({
                "import_batch_id": "Required. Use the value returned by "
                                   "import/preview/."})
        batch_id = self._resolve_import_batch_id(request)

        plan, unrecognised = self._build_plan(request)
        fresh_hash = plan_hash(plan)
        counts = summarise(plan)

        if client_hash != fresh_hash:
            # Nothing is written on a stale hash — never a partial import.
            return Response({
                "success": False,
                "detail": "The data changed since this preview was generated. "
                          "Review the refreshed plan and confirm again.",
                "plan_hash": fresh_hash,
                "counts": counts,
                "unrecognised_columns": unrecognised,
                "rows": public_plan(plan),
            }, status=status.HTTP_409_CONFLICT)

        importable = [e for e in plan if e["classification"] != ERROR]
        created_ids = []
        with transaction.atomic():
            for entry in importable:
                obj = ProposalSubmission(created_by=request.user,
                                         import_batch_id=batch_id,
                                         **entry["_payload"])
                obj.save()
                created_ids.append(obj.id)

            filename = str(request.data.get("filename") or "(unnamed file)")
            ActionLog.objects.create(
                user=request.user,
                action=f"Imported {len(created_ids)} proposal submissions "
                       f"({counts[CREATE]} new, "
                       f"{counts[CREATE_WITH_WARNING]} duplicate-warned, "
                       f"{counts[ERROR]} skipped)",
                # details is a TextField and carries the COMPLETE id list —
                # bulk_delete's ids[:50] truncation loses the audit trail.
                details=f"file={filename}\n"
                        f"import_batch_id={batch_id}\n"
                        f"created_ids={created_ids}\n"
                        f"unrecognised_columns={unrecognised}",
            )

        return Response({
            "success": True,
            "created": len(created_ids),
            "created_ids": created_ids,
            "import_batch_id": str(batch_id),
            "skipped": counts[ERROR],
            "counts": counts,
            "unrecognised_columns": unrecognised,
            "rows": public_plan(plan),
        }, status=status.HTTP_201_CREATED)

    # ── Scope enforcement on the inherited bulk action ────────────────────────

    @action(detail=False, methods=["post"], url_path="bulk_update")
    def bulk_update(self, request, *args, **kwargs):
        """
        Same contract as the shared mixin, with one addition: an id the caller
        cannot see is a 404, not a quiet no-op.

        The mixin already scopes correctly — it intersects the submitted ids with
        get_queryset() — but an entirely out-of-scope batch comes back 200 with
        `permitted: 0`. That is safe (nothing is written) yet it confirms nothing
        either way, and it reads as "your edit applied to 0 rows" rather than
        "that row is not yours". Re-decorated with @action so the route survives
        the override.

        The submitted ids are used ONLY to narrow: the visible set comes from
        get_queryset(), never from the body.
        """
        ids = request.data.get("ids")
        if isinstance(ids, list) and ids and all(isinstance(i, int) for i in ids):
            visible = set(
                self.get_queryset().filter(id__in=ids).values_list("id", flat=True)
            )
            unseen = [i for i in ids if i not in visible]
            if unseen:
                raise NotFound(
                    f"No proposal submission matching id(s) {sorted(unseen)}."
                )
        # Non-int / malformed ids fall through to the mixin's own 400.
        return super().bulk_update(request)

    # ── Row actions ───────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, pk=None):
        """
        POST /api/proposal-submissions/{id}/duplicate/

        Zoho exposes Duplicate as a row action; this is the equivalent. Copies
        all 21 business field values to a new row, re-stamps created_by to the
        caller, and clears updated_by — the copy has never been edited.

        ASSUMPTION: qc_grade and qc_score are COPIED, not cleared. The common use
        is one speaker pitching a second event, where MR's assessment of the
        person still applies. If a duplicate is meant to re-enter QC from
        scratch, clear them here.
        """
        from accounts.models import ActionLog

        # get_object() runs against the scoped queryset, so an out-of-scope
        # source is a 404 before anything is copied.
        source = self.get_object()

        # Defence in depth. The clone inherits the source's event_code, and the
        # source was visible, so this cannot currently fail — it exists so that a
        # future change to the scope rule surfaces here as a 400 rather than
        # quietly minting a row its author cannot see.
        if not may_use_event_code(request.user, source.event_code):
            return Response(
                {"event_code": [
                    f"You are not assigned to event '{source.event_code}'. "
                    f"Assigned events: {sorted(permitted_event_codes(request.user))}"
                ]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            clone = ProposalSubmission(
                **{f: getattr(source, f) for f in BUSINESS_FIELDS},
                created_by=request.user,
                updated_by=None,
            )
            clone.save()
            ActionLog.objects.create(
                user=request.user,
                action=f"Duplicated proposal submission #{source.id} "
                       f"→ #{clone.id}",
                details=f"Speaker: {clone.speaker_name}, "
                        f"Event: {clone.event_code}, "
                        f"qc_grade/qc_score copied from source",
            )

        serializer = self.get_serializer(clone)
        response = Response(serializer.data, status=status.HTTP_201_CREATED)
        # A duplicate is by definition a duplicate — surface the count, still 201.
        return self._attach_duplicate_warning(response, clone)

    @action(detail=False, methods=["delete"], url_path="clear_all",
            permission_classes=[IsHPAccount])
    def clear_all(self, request):
        """
        DELETE /api/proposal-submissions/clear_all/ — HP only
        (accounts.permissions.IsHPAccount).

        The whole table, NOT the caller's scope. Every other read and write here is
        narrowed by scope_queryset(); a "clear all data" that quietly spared another
        team's rows would report success having done part of the job. One account can
        call it, which is the control.

        Paper reviews are left alone. The link runs the other way —
        ProposalSubmission.source_paper_review — so the reviews that generated these
        proposals are a different module's data with its own wipe. Rows generated by
        the bridge come back if those reviews are re-imported; the unique constraint
        one_auto_proposal_per_paper_review is per-review and nothing here breaks it.
        """
        with transaction.atomic():
            deleted = {"proposal_submissions": ProposalSubmission.objects.count()}
            ProposalSubmission.objects.all().delete()
            log_module_wipe(request.user, "PROPOSAL SUBMISSION", deleted)
        return Response({
            "detail": "Successfully removed all proposal submission data.",
            "deleted": deleted,
        })
