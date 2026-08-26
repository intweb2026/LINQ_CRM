"""
ticket_central/views.py
────────────────────────
Ticket CRUD + two-phase submit actions.
"""
import logging
from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.audit import log_module_wipe, reclaim_after_wipe
from accounts.bulk_update import BulkUpdateMixin, build_bulk_update_fields
from accounts.filter_spec import FilterSpecMixin, build_filter_spec_fields
from accounts.period_filter import PeriodFilterMixin
from accounts.permissions import RBACMixin, IsAdminRole, IsHPAccount
from accounts.crm_permissions import crm_permission
from .models import Ticket, TicketSequence
from .serializers import (
    TicketListSerializer,
    TicketDetailSerializer,
    TicketCreateSerializer,
    TicketMRUpdateSerializer,
    TicketDMDUpdateSerializer,
    TicketAdminUpdateSerializer,
)
from .filters import TicketFilter
from .permissions import (
    IsMarketResearchOrAdmin,
    IsDataMiningOrAdmin,
    IsTicketTeamOrAdmin,
)

logger = logging.getLogger(__name__)


class TicketViewSet(PeriodFilterMixin, FilterSpecMixin, BulkUpdateMixin,
                    RBACMixin, viewsets.ModelViewSet):
    """
    GET    /api/tickets/                   — list (paginated, filtered)
    POST   /api/tickets/                   — create (MR only)
    GET    /api/tickets/{id}/              — detail (full read-only)
    PATCH  /api/tickets/{id}/              — update (role-gated serializer)
    POST   /api/tickets/{id}/submit_mr/    — MR submits → status = mr_submitted
    POST   /api/tickets/{id}/submit_dmd/   — DMD submits → status = completed
    POST   /api/tickets/{id}/return_to_mr/ — DMD returns → status = returned
    GET    /api/tickets/stats/             — status counts for dashboard
    """
    permission_classes = [crm_permission("ticket_central")]

    # ?period= presets over "Added Time" — created_at is the column this table
    # sorts by and the one the Zoho report this module replaces was ordered on.
    # It cannot be a filter_spec criterion: accounts/filter_spec.DEFAULT_EXCLUDES
    # keeps created_at/updated_at out of every filterable registry deliberately,
    # and widening that whitelist for one date picker would weaken the policy for
    # all eleven resources. assign_date is NOT used instead — it is blank until
    # Data Mining picks a ticket up, so every unassigned ticket would silently
    # fall out of every window.
    period_date_fields = ("created_at",)

    # ── Mass update ───────────────────────────────────────────────────────────
    # Ticket has no parent FK — every row is independent, so there is no
    # collateral, no split-group UI and no blast-radius warning.
    #
    # Every editable column is wired except the exclusions listed below the
    # registry. The date and count columns are all null=True and come through
    # `nullable`, so an MR can clear a wrongly-entered complete_date across a
    # batch rather than only overwrite it.
    bulk_update_label = "tickets"
    bulk_update_parent_path = None

    # ── Compound filter spec ──────────────────────────────────────────────────
    # Read-only filtering is safe on fields that mass-update deliberately
    # excludes. status, ticket_number and every provenance field ARE filterable
    # here even though writing them is refused: reading a workflow state cannot
    # route around the submit guards, whereas writing it can.
    filter_spec_fields = {
        **build_filter_spec_fields(
            Ticket,
            labels={
                "assigned_mr": "Assigned MR", "assign_name": "Assign Name",
                "assign_name_lx2": "Assign Name (LX-2)", "mr_comments": "MR Comments",
                "dm_comments": "DM Comments", "dm_comments_lx2": "DM Comments (LX-2)",
                "type_of_ticket": "Type of Ticket", "ticket_type": "Ticket Type (DMD)",
                "event_month_year": "Event Month/Year",
                "added_user_text": "Added User",
            },
        ),
        # ── Columns DEFAULT_EXCLUDES held back ────────────────────────────────
        # The surrogate key, the two timestamps and the four provenance columns
        # are excluded from every registry by default, because on most models
        # nobody filters them. This table SHOWS all seven — Added Time, Modified
        # Time, ID, Source_Spreadsheet_ID, Source_Tab, Source_Row_Number and
        # Idempotency_Key are columns in the grid — and a shown column with no
        # server field is not unfiltered, it is filtered in the browser over the
        # rows already fetched. Provenance is exactly what someone reaches for
        # when tracing a bad import, which is precisely when the answer must
        # cover the whole table rather than the current scroll position.
        #
        # has_time is what tells the client to send the END of a day as the
        # upper bound instead of its midnight; without it a filter for "today"
        # silently returns nothing but the first instant of it.
        "created_at": {"type": "date", "label": "Added Time", "has_time": True},
        "updated_at": {"type": "date", "label": "Modified Time", "has_time": True},
        "id": {"type": "number", "label": "ID"},
        "source_spreadsheet_id": {"type": "text", "label": "Source Spreadsheet ID"},
        "source_tab": {"type": "text", "label": "Source Tab"},
        "source_row_number": {"type": "number", "label": "Source Row Number",
                              "nullable": True},
        "idempotency_key": {"type": "text", "label": "Idempotency Key"},
    }

    _BULK_STATIC_FIELDS = build_bulk_update_fields(
        Ticket,
        exclude=(
            # workflow state and provenance — see the block below for why
            "status", "ticket_number",
            "mr_submitted_at", "dmd_submitted_at", "returned_at", "return_reason",
            # added per request by the property below, with live user emails as
            # its choices rather than the free text this would produce
            "assigned_mr",
        ),
        # priority, type_of_ticket and relationship carry NO choices= at the DB
        # level (the D4 notes in models.py:71-85 — Zoho's values don't match a
        # fixed set), so these lists are the ONLY value safety those three have.
        # Nothing at the database or model layer will catch a bad value.
        choices={
            "priority":       list(Ticket.Priority.values),
            "type_of_ticket": list(Ticket.TypeOfTicket.values),
            "relationship":   list(Ticket.Relationship.values),
        },
        labels={
            "assign_name":      "Assign Name",
            "assign_name_lx2":  "Assign Name (LX-2)",
            "actual_count_lx2": "Actual Count (LX-2)",
            "complete_date_lx2": "Complete Date (LX-2)",
            "dm_comments":      "DM Comments",
            "dm_comments_lx2":  "DM Comments (LX-2)",
            "mr_comments":      "MR Comments",
            "type_of_ticket":   "Type of Ticket",
            "ticket_type":      "Ticket Type (DMD)",
            "event_month_year": "Event Month/Year",
            "added_user_text":  "Added User",
            "link_url":         "Link URL",
            "hubspot_entry_date": "HubSpot Entry Date",
        },
    )

    @property
    def bulk_update_fields(self):
        """
        assigned_mr's options are resolved per request from active users rather
        than hardcoded. The column stores an EMAIL (verified against live data:
        every real assignee matches an active user's email), and it is a
        CharField not an FK — the D4 migration-safety decision — so without a
        server-supplied list a mass assign would be free text across up to 1000
        rows, and one typo would silently fragment the assignee set.
        """
        from accounts.models import User
        fields = dict(self._BULK_STATIC_FIELDS)
        fields["assigned_mr"] = {
            "group": "row", "type": "choice", "label": "Assigned MR",
            "choices": list(
                User.objects.filter(is_active=True)
                .exclude(email="")
                .order_by("email")
                .values_list("email", flat=True)
            ),
        }
        return fields

    # EXCLUDED, and why — anything absent from bulk_update_fields is refused:
    #   status         — the three submit actions own every transition
    #                    (submit_mr, submit_dmd, return_to_mr), each guarding on
    #                    current status and stamping *_submitted_by/at.
    #                    Creation now goes straight to MR_SUBMITTED, so DRAFT is
    #                    unreachable via the API; a generic writer would be the
    #                    ONLY way to force a ticket back into DRAFT, re-opening
    #                    submit_mr on an already-submitted ticket and leaving
    #                    provenance null.
    #   ticket_number  — assigned at create by the serializer, and by the
    #                    backfill cron for migrated rows. Never caller-writable.
    #   mr_submitted_at, dmd_submitted_at, returned_at, return_reason
    #                  — provenance, written only by the three submit actions.
    #                    The matching *_by columns are ForeignKeys and are
    #                    dropped by the builder along with created_by.
    #   external_id, idempotency_key, source_spreadsheet_id, source_tab,
    #   source_row_number, id, created_at, updated_at
    #                  — DEFAULT_EXCLUDES in accounts/bulk_update.py: source-system
    #                    keys and audit columns.
    #
    #   assigned_mr is NOT excluded — it is added per request in the property
    #   above, because its options are live users rather than a static list.

    filterset_class = TicketFilter
    # Was fourteen fields, two of them TextField prose. One search was fourteen
    # unanchored substring predicates per row over ~43,000 rows. mr_comments and
    # dm_comments are the expensive pair and are not what people type into the
    # search box; both remain filterable through the compound filter engine, which
    # is explicit about the column it is scanning. Reverting is one line.
    #
    # THIS LIST MUST STAY EXACTLY THE SET COVERED BY THE TRIGRAM INDEXES in
    # Ticket.Meta.indexes. SearchFilter ORs one predicate per field, and the
    # planner can only bitmap-OR the whole disjunction if EVERY branch has an
    # index — one uncovered field drags the entire search back to a sequential
    # scan and silently undoes this work. Adding a field here means adding its
    # GinIndex in the same change.
    #
    # Verified before trimming: all seven removed fields (type_of_ticket,
    # ticket_type, mr_comments, dm_comments, assign_name_lx2, linkedin_keywords,
    # event_location) are present in filter_spec_fields, so none became
    # unreachable.
    search_fields   = [
        "ticket_number", "event_code", "purpose", "organizer",
        "competitor_event_name", "assigned_mr", "assign_name",
    ]
    ordering_fields = ["id", "created_at", "updated_at", "status", "priority"]
    ordering        = ["-created_at"]

    def get_queryset(self):
        qs = Ticket.objects.select_related(
            "created_by", "mr_submitted_by", "dmd_submitted_by",
        )  # assignee fields are CharField now (D4), not FKs — no select_related
        # ⚠ Deliberately NOT calling self.rbac_filter(qs) — tickets are cross-team
        # visibility per product spec; admin, MR, and DMD all see all tickets.
        # Writability stays phase-locked by role + status in the update serializers.
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return TicketCreateSerializer
        if self.action in ("update", "partial_update"):
            user = self.request.user
            if user.is_admin:
                return TicketAdminUpdateSerializer  # admin can write any field
            if user.role == "market_research":
                return TicketMRUpdateSerializer
            if user.role == "data_mining":
                return TicketDMDUpdateSerializer
            return TicketDetailSerializer  # fallback read-only
        if self.action == "retrieve":
            return TicketDetailSerializer
        return TicketListSerializer

    def perform_create(self, serializer):
        from accounts.models import ActionLog
        with transaction.atomic():
            ticket = serializer.save()
            ActionLog.objects.create(
                user=self.request.user,
                action=f"Created ticket {ticket.ticket_number or f'#{ticket.id}'}",
                details=f"Purpose: {ticket.purpose}, Type: {ticket.type_of_ticket}",
            )

    def perform_update(self, serializer):
        from accounts.models import ActionLog
        with transaction.atomic():
            ticket = serializer.save()
            ActionLog.objects.create(
                user=self.request.user,
                action=f"Edited ticket {ticket.ticket_number or f'#{ticket.id}'}",
                details=f"Fields: {list(serializer.validated_data.keys())}"[:200],
            )

    def perform_destroy(self, instance):
        from accounts.models import ActionLog
        with transaction.atomic():
            ActionLog.objects.create(
                user=self.request.user,
                action=f"DELETED ticket {instance.ticket_number or instance.external_id or instance.id}",
                details=f"Purpose: {instance.purpose}, Type: {instance.type_of_ticket}, "
                        f"Status was: {instance.status}",
            )
            super().perform_destroy(instance)

    # ── Phase transitions ─────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="submit_mr",
            permission_classes=[IsMarketResearchOrAdmin])
    def submit_mr(self, request, pk=None):
        """MR submits their section → ticket moves to DMD queue."""
        from accounts.models import ActionLog
        with transaction.atomic():
            ticket = Ticket.objects.select_for_update().get(pk=pk)
            if ticket.status not in (Ticket.Status.DRAFT, Ticket.Status.RETURNED):
                return Response(
                    {"detail": f"Cannot submit from status '{ticket.get_status_display()}'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            ticket.status          = Ticket.Status.MR_SUBMITTED
            ticket.mr_submitted_by = request.user
            ticket.mr_submitted_at = timezone.now()
            ticket.save(update_fields=[
                "status", "mr_submitted_by", "mr_submitted_at", "updated_at",
            ])
            ActionLog.objects.create(
                user=request.user,
                action=f"MR submitted ticket {ticket.ticket_number or f'(ID: {ticket.id})'}",
                details="Status → MR Submitted",
            )
        return Response(TicketDetailSerializer(ticket).data)

    @action(detail=True, methods=["post"], url_path="submit_dmd",
            permission_classes=[IsDataMiningOrAdmin])
    def submit_dmd(self, request, pk=None):
        """DMD submits their section → ticket is complete."""
        from accounts.models import ActionLog
        with transaction.atomic():
            ticket = Ticket.objects.select_for_update().get(pk=pk)
            if ticket.status != Ticket.Status.MR_SUBMITTED:
                return Response(
                    {"detail": f"Cannot submit from status '{ticket.get_status_display()}'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            ticket.status           = Ticket.Status.COMPLETED
            ticket.dmd_submitted_by = request.user
            ticket.dmd_submitted_at = timezone.now()
            ticket.save(update_fields=[
                "status", "dmd_submitted_by", "dmd_submitted_at", "updated_at",
            ])
            ActionLog.objects.create(
                user=request.user,
                action=f"DMD completed ticket {ticket.ticket_number or f'(ID: {ticket.id})'}",
                details="Status → Completed",
            )
        return Response(TicketDetailSerializer(ticket).data)

    @action(detail=True, methods=["post"], url_path="return_to_mr",
            permission_classes=[IsDataMiningOrAdmin])
    def return_to_mr(self, request, pk=None):
        """DMD sends ticket back to MR with a reason."""
        from accounts.models import ActionLog
        reason = request.data.get("reason", "")
        with transaction.atomic():
            ticket = Ticket.objects.select_for_update().get(pk=pk)
            if ticket.status != Ticket.Status.MR_SUBMITTED:
                return Response(
                    {"detail": "Can only return tickets that are in the DMD phase."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            ticket.status        = Ticket.Status.RETURNED
            ticket.returned_by   = request.user
            ticket.returned_at   = timezone.now()
            ticket.return_reason = reason
            ticket.save(update_fields=[
                "status", "returned_by", "returned_at", "return_reason", "updated_at",
            ])
            ActionLog.objects.create(
                user=request.user,
                action=f"Returned ticket {ticket.ticket_number or f'(ID: {ticket.id})'} to MR",
                details=f"Reason: {reason[:200]}",
            )
        return Response(TicketDetailSerializer(ticket).data)

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        """
        GET /api/tickets/stats/[?period=<key>] — counts by status for the tabs.

        The window is applied HERE as well as on the list, because these counts
        label the tabs sitting directly above the filtered table. A tab reading
        "Completed 35,690" over a table showing eleven rows is the same defect as
        an unfiltered aggregate: the two are read together, so they have to answer
        the same question.
        """
        from accounts.period_filter import apply_period

        qs = self.get_queryset()
        _, p_from, p_to = self.resolved_period()
        qs = apply_period(qs, self.period_date_fields, p_from, p_to)
        from django.db.models import Count, Q
        data = qs.aggregate(
            total=Count("id"),
            draft=Count("id", filter=Q(status=Ticket.Status.DRAFT)),
            mr_submitted=Count("id", filter=Q(status=Ticket.Status.MR_SUBMITTED)),
            completed=Count("id", filter=Q(status=Ticket.Status.COMPLETED)),
            returned=Count("id", filter=Q(status=Ticket.Status.RETURNED)),
        )
        return Response(data)

    @action(detail=False, methods=["post"], url_path="run_backfill",
            permission_classes=[IsAdminRole])
    def run_backfill(self, request):
        """Admin-only on-demand trigger for the ticket-number backfill (no 7 AM wait)."""
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        try:
            call_command("backfill_ticket_numbers", stdout=out)
            return Response({"success": True, "output": out.getvalue().strip()})
        except Exception as e:
            logger.exception("run_backfill failed")
            return Response(
                {"success": False, "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["get"], url_path="import_schema")
    def import_schema(self, request):
        """
        GET /api/tickets/import_schema/ — the columns bulk_import accepts.

        Derived from the model by utils.import_fields(), which is the same
        allowlist _coerce_row filters against. The Smart Import wizard used to
        carry its own hand-written list of 15 fields against the ~40 the importer
        accepts, so a Zoho export's DMD and LX-2 columns had nowhere to map and
        were dropped without a word.
        """
        from .utils import import_fields

        return Response({
            "kind": "tickets",
            "fields": [{"key": key, "label": label, "aliases": []}
                       for key, label in import_fields()],
        })

    @action(detail=False, methods=["post"], url_path="bulk_import",
            permission_classes=[IsAdminRole])  # D24 — bulk import is a privileged migration tool
    def bulk_import(self, request):
        """
        POST /api/tickets/bulk_import/
        Body: { rows: [...], duplicate_mode: "allow_all"|"skip_by_external_id"|"upsert_by_external_id"|"skip_by_ticket_number",
                batch_number: int, total_batches: int, dry_run: bool }

        dry_run=true: runs all coercion + validation logic inside a savepoint that
        is rolled back at the end — returns would_insert/would_update/would_skip
        with zero DB writes. Use this to validate a file before the real import.
        """
        from django.db import transaction
        from .utils import _coerce_row

        rows           = request.data.get("rows", [])
        duplicate_mode = request.data.get("duplicate_mode", "allow_all")
        dry_run        = bool(request.data.get("dry_run", False))
        batch_number   = request.data.get("batch_number", 1)

        if not isinstance(rows, list) or not rows:
            return Response(
                {"success": False, "detail": "No rows provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        VALID_MODES = (
            "allow_all",
            "skip_by_external_id",
            "upsert_by_external_id",
            "skip_by_ticket_number",
        )
        if duplicate_mode not in VALID_MODES:
            return Response(
                {"success": False, "detail": f"Invalid mode '{duplicate_mode}'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Pre-load existing keys based on mode
        if duplicate_mode in ("skip_by_external_id", "upsert_by_external_id"):
            dedup_field = "external_id"
            incoming_keys = [
                (r.get("external_id") or "").strip()
                for r in rows
                if (r.get("external_id") or "").strip()
            ]
            existing_set = set(
                Ticket.objects.filter(external_id__in=incoming_keys)
                .values_list("external_id", flat=True)
            )
        elif duplicate_mode == "skip_by_ticket_number":
            dedup_field = "ticket_number"
            incoming_keys = [
                (r.get("ticket_number") or "").strip()
                for r in rows
                if (r.get("ticket_number") or "").strip()
            ]
            existing_set = set(
                Ticket.objects.filter(ticket_number__in=incoming_keys)
                .values_list("ticket_number", flat=True)
            )
        else:  # allow_all
            dedup_field = None
            existing_set = set()

        # A dry run has to hold a REAL transaction open, then roll it back.
        #
        # THE BUG THIS FIXES
        # It used to call transaction.savepoint() here and savepoint_rollback() at the
        # end. transaction.savepoint() is a documented NO-OP while the connection is
        # in autocommit — and it is, because ATOMIC_REQUESTS is not set — so `sid` was
        # None, the rollback did nothing, and a "dry_run" import COMMITTED every row
        # while reporting them as would_insert. The one operation whose entire purpose
        # is to write nothing was the one that wrote silently.
        #
        # transaction.atomic() + set_rollback(True) is the pair that actually holds and
        # discards. The work itself lives in _bulk_import_apply so the rollback wraps
        # ALL of it, and so an exception mid-file cannot leave the block un-exited —
        # CONN_MAX_AGE is 600s here, so a leaked atomic would poison a reused
        # connection rather than just failing one request.
        if dry_run:
            with transaction.atomic():
                inserted, updated, skipped_rows, errors = self._bulk_import_apply(
                    rows, duplicate_mode, dedup_field, existing_set, request)
                transaction.set_rollback(True)
        else:
            inserted, updated, skipped_rows, errors = self._bulk_import_apply(
                rows, duplicate_mode, dedup_field, existing_set, request)

        if dry_run:
            return Response({
                "dry_run":        True,
                "batch_number":   batch_number,
                "duplicate_mode": duplicate_mode,
                "would_insert":   inserted,
                "would_update":   updated,
                "would_skip":     len(skipped_rows),
                "skipped_rows":   skipped_rows[:100],
                "errors":         errors[:100],
            })

        return Response({
            "success":        True,
            "batch_number":   batch_number,
            "duplicate_mode": duplicate_mode,
            "inserted":       inserted,
            "updated":        updated,
            "skipped_count":  len(skipped_rows),
            "skipped_rows":   skipped_rows[:100],
            "errors":         errors[:100],
        })

    def _bulk_import_apply(self, rows, duplicate_mode, dedup_field, existing_set, request):
        """
        Write one batch. Returns (inserted, updated, skipped_rows, errors).

        Extracted from bulk_import unchanged so the caller can wrap the WHOLE batch in
        a transaction for dry_run. Per-row atomic blocks remain: one bad row must not
        take the batch down with it.
        """
        from django.db import transaction
        from .utils import _coerce_row

        inserted, updated, skipped_rows, errors = 0, 0, [], []
        seen_in_batch = set()

        for idx, row in enumerate(rows):
            key = (row.get(dedup_field) or "").strip() if dedup_field else None

            # In-file duplicate detection (uses the same dedup field)
            if key and key in seen_in_batch and duplicate_mode != "allow_all":
                skipped_rows.append({
                    "row_index": idx, "key": key,
                    "reason": f"in_file_duplicate_{dedup_field}",
                })
                continue

            # Cross-database duplicate
            if key and key in existing_set:
                if duplicate_mode == "upsert_by_external_id":
                    try:
                        with transaction.atomic():
                            coerced = _coerce_row(
                                row, exclude={"external_id"},
                                request_user=request.user,
                            )
                            preserved_created_at = coerced.pop("_preserved_created_at", None)
                            Ticket.objects.filter(external_id=key).update(**coerced)
                            if preserved_created_at:
                                Ticket.objects.filter(external_id=key).update(created_at=preserved_created_at)
                        updated += 1
                    except Exception as e:
                        errors.append({"row_index": idx, "key": key, "message": str(e)[:300]})
                else:
                    skipped_rows.append({
                        "row_index": idx, "key": key,
                        "reason": f"duplicate_{dedup_field}",
                    })
                # F3 fix: record key so a second occurrence in this batch is caught
                # by the seen_in_batch check, regardless of upsert or skip path.
                if key:
                    seen_in_batch.add(key)
                continue

            # Create new
            try:
                with transaction.atomic():  # per-row savepoint (CRIT-1 / Finding #1 fix)
                    coerced = _coerce_row(row, request_user=request.user)
                    preserved_created_at = coerced.pop("_preserved_created_at", None)
                    ticket = Ticket.objects.create(
                        created_by=request.user, **coerced,
                    )
                    # Preserve Added Time if provided (D15)
                    if preserved_created_at:
                        Ticket.objects.filter(pk=ticket.pk).update(
                            created_at=preserved_created_at
                        )
                if key:
                    seen_in_batch.add(key)
                inserted += 1
            except Exception as e:
                errors.append({
                    "row_index": idx, "key": key or "(none)",
                    "message": str(e)[:300],
                })

        return inserted, updated, skipped_rows, errors

    @action(detail=False, methods=["post"], url_path="bulk_delete",
            permission_classes=[IsAdminRole])
    def bulk_delete(self, request):
        """Admin-only: delete up to 1000 tickets by ID in one request."""
        from accounts.models import ActionLog
        ids = request.data.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "ids list required"}, status=400)
        if len(ids) > 1000:
            return Response({"detail": "Maximum 1000 IDs per request"}, status=400)

        # Through self.get_queryset(), not Ticket.objects: NO behaviour change today
        # because TicketViewSet.get_queryset() deliberately does not scope (tickets
        # are cross-team visible per product spec, documented there). The point is
        # that if scoping is ever introduced on that queryset, this delete inherits
        # it instead of quietly staying global — which is exactly how the delegate
        # equivalent ended up able to delete out-of-scope rows.
        permitted_ids = list(
            self.get_queryset().filter(id__in=ids).values_list("id", flat=True)
        )
        skipped = len(set(ids)) - len(permitted_ids)
        if not permitted_ids:
            return Response(
                {"detail": "None of the requested records are in your scope.",
                 "deleted": 0, "requested": len(ids), "permitted": 0},
                status=403,
            )

        with transaction.atomic():
            qs    = Ticket.objects.filter(id__in=permitted_ids)
            count = qs.count()
            ActionLog.objects.create(
                user    = request.user,
                action  = f"Bulk deleted {count} tickets",
                details = (
                    f"requested={len(ids)} permitted={count} "
                    f"out_of_scope={skipped} ids={sorted(permitted_ids)}"
                ),
            )
            qs.delete()
        return Response({
            "deleted": count, "requested": len(ids), "permitted": count,
            "out_of_scope": skipped,
        })

    # DELETE, not POST. The verb was the odd one out among the module wipes — two
    # used DELETE, this used POST — and nothing called it yet, so it is standardised
    # here rather than left for the shared frontend button to special-case.
    @action(detail=False, methods=["delete"], url_path="clear_all",
            permission_classes=[IsHPAccount])
    def clear_all(self, request):
        """
        DELETE /api/tickets/clear_all/ — HP only, see accounts.permissions.IsHPAccount.

        Sequences go with the tickets: TicketSequence is what assign_next_ticket_number
        counts from, so leaving it behind would have the first ticket after a wipe
        numbered as though 35,000 still existed.
        """
        with transaction.atomic():
            deleted = {
                "tickets": Ticket.objects.count(),
                "sequences": TicketSequence.objects.count(),
            }
            Ticket.objects.all().delete()
            TicketSequence.objects.all().delete()
            log_module_wipe(request.user, "TICKET CENTRAL", deleted)
        # Outside the atomic block: VACUUM cannot run in a transaction. Tickets is the
        # largest table in the CRM at 42,912 rows, so a wipe that left its pages
        # behind would keep every later query paying for them — see
        # accounts/audit.py reclaim_after_wipe.
        reclaim_after_wipe(Ticket._meta.db_table, TicketSequence._meta.db_table)
        return Response({
            "detail": "Successfully removed all ticket central data.",
            "deleted": deleted,
            "sequences_reset": True,
        })
