from datetime import datetime
from decimal import Decimal
from django.db import transaction
from django.db.models import Count, Q, Sum, DecimalField
from django.db.models.functions import Coalesce
# `status` was missing while clear_all's exception path already referenced
# status.HTTP_500_INTERNAL_SERVER_ERROR — so a failure inside the wipe raised
# NameError from the handler meant to report it, turning a clean 500-with-reason
# into an unhandled error.
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.audit import log_module_wipe, reclaim_after_wipe
from accounts.permissions import RBACMixin, IsAdminRole, IsSalesOrAdmin, IsHPAccount
from accounts.bulk_update import BulkUpdateMixin, build_bulk_update_fields
from accounts.crm_permissions import crm_permission, has_all_records
from accounts.filter_spec import FilterSpecMixin, build_filter_spec_fields
from .models import Event
from .serializers import EventListSerializer, EventDetailSerializer, EventWriteSerializer
from .filters import EventFilter


class EventViewSet(FilterSpecMixin, BulkUpdateMixin, RBACMixin, viewsets.ModelViewSet):
    permission_classes = [crm_permission("events")]

    # ── Compound filter spec ──────────────────────────────────────────────────
    # Everything is filterable here, INCLUDING event_code and the nine
    # save()-derived fields. Only WRITING those was dangerous; reading them is
    # not. Note: BookEvent.edition holds Excel serial dates on 5,854 rows, so
    # numeric comparisons against edition-like data will behave oddly. Surfaced,
    # not fixed — it belongs to the separate event_code/edition work item.
    filter_spec_fields = build_filter_spec_fields(
        Event,
        labels={
            "event_code": "Event Code", "official_event_name": "Official Event Name",
            "web_bookings": "Web Bookings",
            "accepting_web_bookings": "Accepting Web Bookings (derived)",
            "name": "Name (derived)", "official_name": "Official Name (derived)",
            "city": "City (derived)", "country": "Country (derived)",
            "venue": "Venue (derived)", "sales_team": "SCA (derived)",
            "tele_marketing_team": "Telemarketing Team (derived)",
            "market_research_team": "Market Research Team (derived)",
        },
    )

    # ── Mass update ───────────────────────────────────────────────────────────
    # Event has no parent FK. BookEvent links to it by event_code TEXT, not a
    # relation, so there is no collateral set and no split-group UI.
    #
    # Every editable column is wired EXCEPT the exclusions below. Event.save()
    # (models.py:85-154) derives NINE fields; only SOURCE fields are offered,
    # because writing a derived field directly would be silently undone on the
    # next save of its source.
    #
    #   official_event_name  -> name, official_name   (falls back to event_code)
    #   location             -> city, country, venue
    #   web_bookings         -> accepting_web_bookings
    #   telemarketing_team   -> tele_marketing_team
    #   market_research_senior -> market_research_team
    #   sales_executive     <-> sales_team   (BIDIRECTIONAL, fuzzy user match,
    #                                         plus a per-object SELECT at :105-107)
    #
    # EXCLUDED and why:
    #   event_code   — identity, and 86 of 215 BookEvent codes already fail to
    #                  match this table (40.8% of invoices). Mass-editing codes
    #                  would deepen an outstanding data-integrity problem.
    #                  It also feeds name/official_name when official_event_name
    #                  is blank (models.py:87-92).
    #   edition      — not a field on Event; the corrupt editions live on
    #                  BookEvent (5,854 rows hold Excel serial dates). Same
    #                  outstanding problem; nothing here may touch it.
    #   name, official_name, accepting_web_bookings, city, country, venue,
    #   tele_marketing_team, market_research_team, sales_team
    #                — all derived in save(). Callers set the SOURCE field.
    #   sales_executive — an FK whose save() path fuzzy-matches users and writes
    #                  back to sales_team; too much hidden behaviour for a
    #                  generic writer. Dropped by the builder with every other FK
    #                  rather than by name, and deferred deliberately.
    #   id / created_at / updated_at / import_batch_id
    #                — DEFAULT_EXCLUDES in accounts/bulk_update.py.
    bulk_update_label = "events"
    bulk_update_parent_path = None

    # Derived from the model, minus the exclusions above. `nullable` mirrors
    # null=True per column, so end_date and website_live_date can be cleared and
    # event_date cannot.
    bulk_update_fields = build_bulk_update_fields(
        Event,
        exclude=(
            # identity
            "event_code",
            # the nine fields Event.save() derives — callers set the SOURCE
            "name", "official_name", "accepting_web_bookings",
            "city", "country", "venue",
            "tele_marketing_team", "market_research_team", "sales_team",
        ),
        labels={
            "official_event_name":    "Official Event Name",
            "web_bookings":           "Web Bookings",
            "vr1_sent_status":        "VR1 Sent Status",
            "email_marketing_name":   "Email Marketing Name",
            "market_research_senior": "Market Research Senior",
            "market_research_junior": "Market Research Junior",
            "telemarketing_team":     "Telemarketing Team",
            "event_management_team":  "Event Management Team",
            "spex_team":              "SPEX Team",
            "master_code":            "Master Code",
            "website_live_date":      "Website Live Date",
            "nearest_related_event":  "Nearest Related Event",
            "related_event_1":        "Related Event 1",
            "related_event_2":        "Related Event 2",
            "related_event_3":        "Related Event 3",
            "upcoming_event_1":       "Upcoming Event 1",
            "upcoming_event_2":       "Upcoming Event 2",
            "upcoming_event_3":       "Upcoming Event 3",
        },
    )

    # Every save()-derivation of a wired field MUST be declared here, or the
    # preview understates what the change actually does.
    bulk_update_side_effects = {
        ("web_bookings", False): (
            "also sets accepting_web_bookings → False; the website booking "
            "webhook will stop accepting registrations for these events"
        ),
        ("web_bookings", "false"): (
            "also sets accepting_web_bookings → False; the website booking "
            "webhook will stop accepting registrations for these events"
        ),
        ("web_bookings", True): "also sets accepting_web_bookings → True",
        ("web_bookings", "true"): "also sets accepting_web_bookings → True",
    }

    # Every save()-derivation of a wired field that fires for ANY value, keyed by
    # field alone. The static dict above cannot express those — it is an exact
    # (field, value) lookup — so they live here.
    _ANY_VALUE_SIDE_EFFECTS = {
        "location":               "also overwrites city, country and venue",
        "official_event_name":    "also overwrites name and official_name",
        "telemarketing_team":     "also overwrites tele_marketing_team",
        "market_research_senior": "also overwrites market_research_team",
    }

    def get_bulk_update_side_effects(self, field, raw_value):
        """
        location and official_event_name overwrite their derived fields for ANY
        value, so they cannot be keyed by (field, value) in the static dict.
        telemarketing_team and market_research_senior behave the same way, and
        were undeclared while they were unwired.
        """
        any_value = self._ANY_VALUE_SIDE_EFFECTS.get(field)
        if any_value:
            return [any_value]
        effect = self.bulk_update_side_effects.get((field, raw_value))
        return [effect] if effect else []
    filterset_class = EventFilter
    search_fields   = ["event_code", "name", "city"]
    ordering_fields = ["id", "event_date", "name", "event_code"]
    ordering        = ["-event_date"]

    def get_queryset(self):
        user = self.request.user
        qs = Event.objects.select_related("sales_executive").prefetch_related("assigned_users")
        # has_all_records is the permission grid's row-scope cell:
        # every event, without the blanket widening that is_admin or an
        # is_all_access team would also apply to every other module.
        if not user.is_admin and not has_all_records(user, "events"):
            # `__in` over the caller's data scope, not `= user`. That scope is
            # just the caller for everybody except a lead, who also carries the
            # active accounts naming them as reporting manager, so this stays
            # byte-for-byte the old query for anyone nobody reports to. See
            # accounts.models.User.data_scope_user_ids.
            scope_ids = user.data_scope_user_ids() or [user.pk]
            qs = qs.filter(
                Q(assigned_users__in=scope_ids) | Q(sales_executive__in=scope_ids)
            ).distinct()
        return qs

    def get_serializer_class(self):
        if self.action == "retrieve":
            return EventDetailSerializer
        if self.action in ("create", "update", "partial_update"):
            return EventWriteSerializer
        return EventListSerializer

    def create(self, request, *args, **kwargs):
        ser = EventWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        event = ser.save()
        return Response(EventListSerializer(event).data, status=201)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        ser = EventWriteSerializer(instance, data=request.data, partial=partial)
        ser.is_valid(raise_exception=True)
        return Response(EventListSerializer(ser.save()).data)

    @action(detail=False, methods=["get"])
    def years(self, request):
        """GET /api/events/years/ — distinct years from event_date, sorted descending."""
        from django.db.models.functions import ExtractYear
        qs = self.get_queryset().filter(event_date__isnull=False)
        years = (
            qs.annotate(year=ExtractYear("event_date"))
            .values_list("year", flat=True)
            .distinct()
            .order_by("-year")
        )
        return Response(list(years))

    @action(detail=False, methods=["get"])
    def all_edition_growth(self, request):
        """
        GET /api/events/all_edition_growth/
        Returns YoY growth data for every event that has historical editions
        or multi-year bookings. Used to populate the Reports › Growth table.
        """
        from historical_event_registry.models import HistoricalEventReference
        from historical_event_registry.growth_service import YearOnYearGrowthCalculator
        from book_event.models import BookEvent
        from django.db.models.functions import ExtractYear

        # Collect event codes with historical references
        hist_codes = set(
            HistoricalEventReference.objects
            .values_list("normalized_event_code", flat=True)
            .distinct()
        )

        # Also include events with bookings from 2+ distinct years.
        # Raw booking codes carry a year suffix (e.g. 'SPU - VV26'); strip it
        # so we group by the canonical base code that matches Event.event_code.
        from historical_event_registry.utils import normalize_event_code as _norm
        from django.db.models import Count as _Count
        multi_raw = (
            BookEvent.objects.exclude(event_date__isnull=True)
            .annotate(yr=ExtractYear("event_date"))
            .values("event_code")
            .annotate(year_count=_Count("yr", distinct=True))
            .filter(year_count__gte=2)
            .values_list("event_code", flat=True)
        )
        multi_year_codes = {_norm(c) for c in multi_raw if _norm(c)}

        all_codes = hist_codes | multi_year_codes
        if not all_codes:
            return Response([])

        # Build event map: normalized base code → Event instance
        event_map = {e.event_code: e for e in self.get_queryset().filter(event_code__in=all_codes)}

        results = []
        for code in sorted(all_codes):
            event = event_map.get(code)
            calc  = YearOnYearGrowthCalculator(event_code=code, event=event)
            results.append(calc.calculate())

        # Sort by total_sales_all_years descending
        results.sort(key=lambda r: r["total_sales_all_years"], reverse=True)
        return Response(results)

    @action(detail=True, methods=["get"])
    def edition_growth(self, request, pk=None):
        """GET /api/events/{id}/edition_growth/ — full YoY growth for one event."""
        from historical_event_registry.growth_service import YearOnYearGrowthCalculator, EditionGrowthValidator
        event  = self.get_object()
        result = EditionGrowthValidator(event_code=event.event_code, event=event).validate_and_fix()
        return Response(result)

    @action(detail=True, methods=["get"])
    def historical_editions(self, request, pk=None):
        """GET /api/events/{id}/historical_editions/ — all historical editions with live metrics."""
        event = self.get_object()
        from historical_event_registry.edition_service import HistoricalEditionDataService
        service  = HistoricalEditionDataService(event_code=event.event_code)
        editions = service.get_editions()

        # Exclude the current event's own year so it doesn't appear as a past edition
        current_year = event.event_date.year if event.event_date else None
        if current_year:
            editions = [e for e in editions if e["year"] != current_year]

        return Response({"event_code": event.event_code, "editions": editions})

    @action(detail=True, methods=["get"])
    def edition_bookings(self, request, pk=None):
        """
        GET /api/events/{id}/edition_bookings/         → all editions with invoice-date metrics
        GET /api/events/{id}/edition_bookings/?year=N  → full booking list for edition year N
        """
        from historical_event_registry.booking_engine import EventEditionBookingEngine
        event  = self.get_object()
        engine = EventEditionBookingEngine(event_code=event.event_code, event=event)

        year_param = request.query_params.get("year")
        if year_param:
            try:
                year = int(year_param)
            except ValueError:
                return Response({"error": "Invalid year parameter"}, status=400)
            return Response(engine.get_edition_bookings(year))

        return Response(engine.get_summary())

    @action(detail=True, methods=["get"])
    def stats(self, request, pk=None):
        """GET /api/events/{id}/stats/ — booking and revenue breakdown."""
        event = self.get_object()
        from book_event.models import BookEvent
        from book_delegate.models import BookDelegate
        from django.db.models import DecimalField

        bookings = BookEvent.objects.filter(event_code=event.event_code)

        rev_by_status = list(
            bookings.values("payment_status").annotate(
                count=Count("id"),
                total=Coalesce(
                    Sum("total_amount"),
                    Decimal("0"),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                ),
            )
        )

        return Response({
            "event_code":     event.event_code,
            "event_name":     event.name,
            "booking_count":  bookings.count(),
            "delegate_count": BookDelegate.objects.filter(event_code=event.event_code).count(),
            "by_payment_status": [
                {"status": r["payment_status"], "count": r["count"], "revenue": float(r["total"])}
                for r in rev_by_status
            ],
        })

    @action(detail=False, methods=["post"], url_path="bulk_import")
    def bulk_import(self, request):
        """
        POST /api/events/bulk_import/
        Bulk-insert up to 500 Event rows per call.
        Body: { rows: [...], duplicate_strategy: "skip"|"upsert", batch_number: int }
        """
        rows               = request.data.get("rows", [])
        strategy           = request.data.get("duplicate_strategy", "skip")
        batch_number       = request.data.get("batch_number", 1)

        if not rows:
            return Response({"success": False, "detail": "No rows provided."}, status=400)

        DATE_FMTS = ["%d %b %Y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y",
                     "%d-%b-%Y", "%d %B %Y"]

        def _parse_date(val):
            if not val:
                return None
            s = str(val).strip()
            for fmt in DATE_FMTS:
                try:
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
            return None

        def _clean(d, key, default=""):
            return str(d.get(key) or default).strip()

        from accounts.user_resolution import OwnerResolver

        # One read of the user table for the whole import, not one per row per
        # column, which is the reason UserResolver is a class.
        _owner_resolver = OwnerResolver()

        def _resolve_user(name_str):
            """
            The user an imported owner name names, or None.

            Shares accounts.user_resolution.OwnerResolver with Event.save() and
            the routing command, so an event imported from a sheet and the same
            event edited in the UI cannot end up owned by two different people. It
            replaces a local two way substring compare returning the first hit,
            which mattered more here than anywhere else: the names resolved in
            this loop are also written into `assigned_users`, and that m2m grants
            row visibility, so a wrong partial match did not merely mislabel a
            column, it showed one person another person's events.

            An ambiguous name resolves to None and the row keeps its plain text,
            exactly as an unknown name always did.
            """
            user, _reason = _owner_resolver.resolve(name_str)
            return user

        inserted = 0
        skipped = 0
        skipped_records = []
        errors = []
        auto_gen_rows = []

        for i, row in enumerate(rows):
            event_code = _clean(row, "event_code").upper()
            auto_code = False
            if not event_code:
                import uuid
                event_code = f"IMP-EV-{uuid.uuid4().hex[:10].upper()}"
                auto_code = True

            name = _clean(row, "name")
            auto_name = False
            if not name:
                name = f"Untitled Event - {event_code}"
                auto_name = True

            try:
                with transaction.atomic():
                    # Parse dates
                    event_date_val = row.get("event_date")
                    event_date = _parse_date(event_date_val)
                    auto_date = False
                    if not event_date:
                        from django.utils import timezone
                        event_date = timezone.now().date()
                        auto_date = True

                    end_date = _parse_date(row.get("end_date"))

                    # Upgraded 31 Fields
                    location = _clean(row, "location")
                    website = _clean(row, "website")
                    nearest_related_event = _clean(row, "nearest_related_event")
                    event_type = _clean(row, "event_type")
                    website_live_date = _parse_date(row.get("website_live_date"))
                    vr1_sent_status = _clean(row, "vr1_sent_status")
                    # Plain trimmed strings, matching how the CSV loader treats
                    # them at management/commands/update_events_csv.py:77-78. No
                    # _resolve_user and no assigned_users entry, unlike
                    # sales_check, so importing them cannot widen row visibility.
                    content_check = _clean(row, "content_check")
                    marketing_check = _clean(row, "marketing_check")
                    sales_team = _clean(row, "sales_team")
                    team_leader = _clean(row, "team_leader")
                    market_research_senior = _clean(row, "market_research_senior")
                    market_research_junior = _clean(row, "market_research_junior")
                    event_management_team = _clean(row, "event_management_team")
                    official_event_name = _clean(row, "official_event_name")
                    email_marketing_name = _clean(row, "email_marketing_name")
                    branding_name = _clean(row, "branding_name")
                    annualisation = _clean(row, "annualisation")
                    date_format = _clean(row, "date_format")
                    related_event_1 = _clean(row, "related_event_1")
                    related_event_2 = _clean(row, "related_event_2")
                    related_event_3 = _clean(row, "related_event_3")
                    upcoming_event_1 = _clean(row, "upcoming_event_1")
                    upcoming_event_2 = _clean(row, "upcoming_event_2")
                    upcoming_event_3 = _clean(row, "upcoming_event_3")
                    status = _clean(row, "status") or Event.Status.DRAFT

                    # Accepting Web Bookings
                    awb_raw = _clean(row, "web_bookings") or _clean(row, "accepting_web_bookings")
                    web_bookings = awb_raw.lower() in ("yes", "true", "1")

                    # Resolve Sales Executive
                    #
                    # Falls back to the SCA column, because most sheets carry only
                    # that one. Event.save() would resolve it anyway, but it would
                    # build a resolver per row; resolving here reuses the one built
                    # for the whole import, and it puts the person into
                    # assigned_users as well, so the event is visible to them by
                    # both of the links events/views.py get_queryset checks.
                    se_name = _clean(row, "sales_executive")
                    sales_exec = _resolve_user(se_name) or _resolve_user(sales_team)

                    # Resolve other team members for M2M assignments and string values
                    spex_user = _resolve_user(_clean(row, "spex_team"))
                    tele_marketing_user = _resolve_user(_clean(row, "telemarketing_team") or _clean(row, "tele_marketing_team"))
                    market_research_senior_user = _resolve_user(market_research_senior)
                    market_research_junior_user = _resolve_user(market_research_junior)
                    sales_check_user = _resolve_user(_clean(row, "sales_check"))
                    team_leader_user = _resolve_user(team_leader)
                    event_management_user = _resolve_user(event_management_team)

                    # Assign resolved user names to fields for absolute integrity
                    if spex_user:
                        spex_team = spex_user.get_full_name() or spex_user.username
                    else:
                        spex_team = _clean(row, "spex_team")

                    if tele_marketing_user:
                        telemarketing_team = tele_marketing_user.get_full_name() or tele_marketing_user.username
                    else:
                        telemarketing_team = _clean(row, "telemarketing_team") or _clean(row, "tele_marketing_team")

                    if market_research_senior_user:
                        market_research_senior = market_research_senior_user.get_full_name() or market_research_senior_user.username
                    else:
                        market_research_senior = _clean(row, "market_research_senior")

                    if market_research_junior_user:
                        market_research_junior = market_research_junior_user.get_full_name() or market_research_junior_user.username
                    else:
                        market_research_junior = _clean(row, "market_research_junior")

                    if sales_check_user:
                        sales_check = sales_check_user.get_full_name() or sales_check_user.username
                    else:
                        sales_check = _clean(row, "sales_check")

                    if team_leader_user:
                        team_leader = team_leader_user.get_full_name() or team_leader_user.username
                    else:
                        team_leader = _clean(row, "team_leader")

                    if event_management_user:
                        event_management_team = event_management_user.get_full_name() or event_management_user.username
                    else:
                        event_management_team = _clean(row, "event_management_team")

                    existing = Event.objects.filter(event_code=event_code).first()

                    assigned_users_to_set = []
                    if sales_exec:
                        assigned_users_to_set.append(sales_exec)
                    if spex_user:
                        assigned_users_to_set.append(spex_user)
                    if tele_marketing_user:
                        assigned_users_to_set.append(tele_marketing_user)
                    if market_research_senior_user:
                        assigned_users_to_set.append(market_research_senior_user)
                    if market_research_junior_user:
                        assigned_users_to_set.append(market_research_junior_user)
                    if sales_check_user:
                        assigned_users_to_set.append(sales_check_user)
                    if team_leader_user:
                        assigned_users_to_set.append(team_leader_user)
                    if event_management_user:
                        assigned_users_to_set.append(event_management_user)

                    # Deduplicate assigned users
                    assigned_users_to_set = list(set(assigned_users_to_set))

                    if existing:
                        if strategy == "upsert":
                            existing.name = name
                            existing.official_event_name = official_event_name
                            existing.event_date = event_date
                            existing.end_date = end_date or existing.end_date
                            existing.location = location
                            existing.website = website
                            existing.web_bookings = web_bookings
                            existing.nearest_related_event = nearest_related_event
                            existing.event_type = event_type
                            existing.website_live_date = website_live_date or existing.website_live_date
                            existing.sales_check = _clean(row, "sales_check") or existing.sales_check
                            existing.content_check = content_check or existing.content_check
                            existing.marketing_check = marketing_check or existing.marketing_check
                            existing.vr1_sent_status = vr1_sent_status or existing.vr1_sent_status
                            existing.sales_team = sales_team or existing.sales_team
                            existing.team_leader = team_leader or existing.team_leader
                            existing.telemarketing_team = _clean(row, "telemarketing_team") or existing.telemarketing_team
                            existing.spex_team = _clean(row, "spex_team") or existing.spex_team
                            existing.market_research_senior = market_research_senior or existing.market_research_senior
                            existing.market_research_junior = market_research_junior or existing.market_research_junior
                            existing.event_management_team = event_management_team or existing.event_management_team
                            existing.email_marketing_name = email_marketing_name or existing.email_marketing_name
                            existing.branding_name = branding_name or existing.branding_name
                            existing.annualisation = annualisation or existing.annualisation
                            existing.date_format = date_format or existing.date_format
                            existing.related_event_1 = related_event_1 or existing.related_event_1
                            existing.related_event_2 = related_event_2 or existing.related_event_2
                            existing.related_event_3 = related_event_3 or existing.related_event_3
                            existing.upcoming_event_1 = upcoming_event_1 or existing.upcoming_event_1
                            existing.upcoming_event_2 = upcoming_event_2 or existing.upcoming_event_2
                            existing.upcoming_event_3 = upcoming_event_3 or existing.upcoming_event_3
                            existing.status = status or existing.status
                            existing.sales_executive = sales_exec or existing.sales_executive

                            existing.save()

                            if assigned_users_to_set:
                                existing.assigned_users.set(assigned_users_to_set)

                            inserted += 1
                        else:
                            skipped += 1
                            skipped_records.append({
                                "row_index": i + 1,
                                "event_code": event_code,
                                "official_event_name": official_event_name or name
                            })
                    else:
                        event = Event.objects.create(
                            event_code=event_code,
                            event_date=event_date,
                            end_date=end_date,
                            location=location,
                            website=website,
                            web_bookings=web_bookings,
                            nearest_related_event=nearest_related_event,
                            event_type=event_type,
                            website_live_date=website_live_date,
                            sales_check=_clean(row, "sales_check"),
                            content_check=content_check,
                            marketing_check=marketing_check,
                            vr1_sent_status=vr1_sent_status,
                            sales_team=sales_team,
                            team_leader=team_leader,
                            telemarketing_team=_clean(row, "telemarketing_team"),
                            spex_team=_clean(row, "spex_team"),
                            market_research_senior=market_research_senior,
                            market_research_junior=market_research_junior,
                            event_management_team=event_management_team,
                            official_event_name=official_event_name,
                            email_marketing_name=email_marketing_name,
                            branding_name=branding_name,
                            annualisation=annualisation,
                            date_format=date_format,
                            related_event_1=related_event_1,
                            related_event_2=related_event_2,
                            related_event_3=related_event_3,
                            upcoming_event_1=upcoming_event_1,
                            upcoming_event_2=upcoming_event_2,
                            upcoming_event_3=upcoming_event_3,
                            status=status,
                            sales_executive=sales_exec,
                        )

                        if assigned_users_to_set:
                            event.assigned_users.set(assigned_users_to_set)

                        inserted += 1

                    if auto_code or auto_name or auto_date:
                        se_display = _clean(row, "sales_executive") or (
                            f"{sales_exec.get_full_name() or sales_exec.username}" if sales_exec else "Unknown"
                        )
                        auto_gen_rows.append({
                            "event_code": event_code,
                            "sales_executive": se_display,
                            "auto_code": auto_code,
                            "auto_name": auto_name,
                            "auto_date": auto_date,
                        })

            except Exception as exc:
                errors.append({"row_index": i, "event_code": event_code, "message": str(exc)})

        # Send alert email for any auto-generated fields.
        #
        # Gated on IMPORT_ALERT_EMAILS_ENABLED, which defaults False — see the
        # matching guard in book_event/views.py and the rationale on the setting
        # itself. This fires once per CALL, so a chunked import of events missing a
        # code / name / date would otherwise send one message per chunk.
        from django.conf import settings as django_settings
        if auto_gen_rows and django_settings.IMPORT_ALERT_EMAILS_ENABLED:
            try:
                from django.core.mail import send_mail
                recipient = getattr(django_settings, "IMPORT_ALERT_EMAIL", "harrison.peck@iq-hub.com")
                lines = []
                for entry in auto_gen_rows:
                    details = []
                    if entry.get("auto_code"):
                        details.append(f"Auto-Code: {entry['event_code']}")
                    else:
                        details.append(f"Event Code: {entry['event_code']}")

                    if entry.get("auto_name"):
                        details.append("Empty Name filled as Untitled")
                    if entry.get("auto_date"):
                        details.append("Empty Date filled as Today")

                    lines.append(
                        f"  • " + " | ".join(details) +
                        f"  |  Added by: @{entry['sales_executive']}"
                    )
                body = (
                    f"Hi Harrison,\n\n"
                    f"{len(auto_gen_rows)} new event entr{'y was' if len(auto_gen_rows) == 1 else 'ies were'} "
                    f"imported with missing required fields — auto-generated or default values assigned:\n\n"
                    + "\n".join(lines)
                    + "\n\nThese entries were created via the Smart Import tool and may need manual corrections assigned.\n\n"
                    f"— Linq CRM"
                )
                send_mail(
                    subject=f"[Linq CRM] {len(auto_gen_rows)} Event Import{'s' if len(auto_gen_rows) != 1 else ''} With Auto-Generated Fields",
                    message=body,
                    from_email=django_settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient],
                    fail_silently=True,
                )
            except Exception:
                pass  # never block the response over an email failure

        return Response({
            "success":            len(errors) == 0,
            "batch_number":       batch_number,
            "inserted":           inserted,
            "skipped_duplicates": skipped,
            "skipped_records":    skipped_records,
            "errors":             errors[:20],
        })

    @action(detail=False, methods=["delete"], url_path="clear_all",
            permission_classes=[IsHPAccount])
    def clear_all(self, request):
        """
        DELETE /api/events/clear_all/ — HP only, see accounts.permissions.IsHPAccount.

        Only the catalogue itself. Bookings are NOT touched: BookEvent stores its
        event code as text rather than a foreign key, so deleting the catalogue
        leaves every booking in place with a code that no longer resolves to an
        event — which is why the confirmation in the UI says so, and why clearing
        bookings is a separate action on its own module.
        """
        try:
            with transaction.atomic():
                deleted = {"events": Event.objects.count()}
                Event.objects.all().delete()
                log_module_wipe(request.user, "EVENTS", deleted)
            # Outside the atomic block: VACUUM cannot run in a transaction. See
            # accounts/audit.py reclaim_after_wipe for why a DELETE alone leaves the
            # table as slow to scan as it was when full.
            reclaim_after_wipe(Event._meta.db_table)
            return Response({
                "detail": "Successfully removed all event data.",
                "deleted": deleted,
            })
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
