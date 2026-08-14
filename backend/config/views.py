"""
config/views.py
────────────────
Global search + dashboard stats — RBAC-scoped per user role.
"""
from collections import Counter
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import period_filter
from book_delegate.models import BookDelegate
from book_delegate.serializers import BookDelegateListSerializer
from book_event.models import BookEvent
from book_event.serializers import BookEventListSerializer
from companies.models import Company
from companies.serializers import CompanySerializer
from events.models import Event
from events.serializers import EventListSerializer
from teams.models import Team


def _event_codes(user):
    """None = unrestricted (admin). List = allowed codes (sales)."""
    if user.is_admin:
        return None
    return user.assigned_event_codes() or []


# The date-range vocabulary is shared with every list endpoint that offers the
# same presets — see accounts/period_filter.py for the keys, the rolling-window
# rule and why it is not a filter_spec criterion. Re-exported here because this
# module's name is what the dashboard tests and callers already import.
PERIOD_DAYS = period_filter.PERIOD_DAYS
period_window = period_filter.period_window


class GlobalSearchView(APIView):
    """
    GET /api/search/?q=<term>[&type=all|invoice|delegate|event|company][&limit=20]

    Searches across all modules. Results are RBAC-scoped for sales users.
    Company search is admin-only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        q = request.query_params.get("q", "").strip()
        search_type = request.query_params.get("type", "all")
        limit = min(int(request.query_params.get("limit", 20)), 100)

        if not q or len(q) < 2:
            return Response({"detail": "Query must be at least 2 characters."}, status=400)

        codes = _event_codes(request.user)
        results = {}

        if search_type in ("all", "invoice"):
            qs = BookEvent.objects.filter(
                Q(invoice_number__icontains=q)
                | Q(event_code__icontains=q)
                | Q(contact_name__icontains=q)
                | Q(contact_email__icontains=q)
                | Q(company_name__icontains=q)
            )
            if codes is not None:
                qs = qs.filter(event_code__in=codes)
            items = list(qs[:limit])
            results["invoices"] = {
                "count": len(items),
                "items": BookEventListSerializer(items, many=True).data,
            }

        if search_type in ("all", "delegate"):
            qs = BookDelegate.objects.select_related("invoice", "company").filter(
                Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(email__icontains=q)
                | Q(invoice__invoice_number__icontains=q)
                | Q(company__name__icontains=q)
            )
            if codes is not None:
                qs = qs.filter(event_code__in=codes)
            items = list(qs[:limit])
            results["delegates"] = {
                "count": len(items),
                "items": BookDelegateListSerializer(items, many=True).data,
            }

        if search_type in ("all", "event"):
            qs = Event.objects.filter(
                Q(event_code__icontains=q) | Q(name__icontains=q) | Q(city__icontains=q)
            )
            if codes is not None:
                qs = qs.filter(event_code__in=codes)
            items = list(qs[:limit])
            results["events"] = {
                "count": len(items),
                "items": EventListSerializer(items, many=True).data,
            }

        if search_type in ("all", "company") and request.user.is_admin:
            qs = Company.objects.filter(
                Q(name__icontains=q) | Q(city__icontains=q) | Q(country__icontains=q)
            )[:limit]
            results["companies"] = {
                "count": len(qs),
                "items": CompanySerializer(qs, many=True).data,
            }

        total = sum(v.get("count", 0) for v in results.values())
        return Response({"query": q, "total": total, "results": results})


class DashboardStatsView(APIView):
    """
    GET /api/stats/dashboard/

    Revenue and volume stats. RBAC-scoped for sales users.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        codes = _event_codes(request.user)

        inv_qs = BookEvent.objects.all()
        del_qs = BookDelegate.objects.all()
        ev_qs  = Event.objects.all()

        if codes is not None:
            inv_qs = inv_qs.filter(event_code__in=codes)
            del_qs = del_qs.filter(event_code__in=codes)
            ev_qs  = ev_qs.filter(event_code__in=codes)

        inv_stats = inv_qs.aggregate(
            total=Count("id"),
            paid=Count("id", filter=Q(payment_status="Paid")),
            pending=Count("id", filter=Q(payment_status="Pending")),
            cancelled=Count("id", filter=Q(payment_status="Cancelled")),
            revenue_paid=Coalesce(Sum("total_amount", filter=Q(payment_status="Paid")), Decimal("0")),
            revenue_pending=Coalesce(Sum("total_amount", filter=Q(payment_status="Pending")), Decimal("0")),
        )

        from django.utils import timezone
        today = timezone.now().date()

        ev_stats = ev_qs.aggregate(
            total=Count("id"),
            live=Count("id", filter=Q(event_date__gte=today)),
            upcoming=Count("id", filter=Q(event_date__lt=today)), # Misnamed in frontend but keeping for compat
        )

        top_events = (
            inv_qs.filter(payment_status="Paid")
            .values("event_code")
            .annotate(bookings=Count("id"))
            .order_by("-bookings")[:5]
        )

        return Response({
            "events": ev_stats,
            "invoices": {
                **inv_stats,
            },
            "delegates": {"total": del_qs.count()},
            "companies": Company.objects.count() if request.user.is_admin else None,
            "top_events_by_revenue": [
                {
                    "event_code": e["event_code"],
                    "bookings":  e["bookings"],
                }
                for e in top_events
            ],
        })


class DashboardAggregateView(APIView):
    """
    GET /api/stats/dashboard_aggregate/[?period=<key>]

    Everything the Dashboard needs that is a GROUP BY, computed in SQL.
    `period` is one of PERIOD_DAYS; unknown keys are a 400, never a silent
    fall-back to "all" — a filter that quietly ignores its input reads as a
    broken filter.

    WHY THIS EXISTS
    The frontend built all of this in the browser from `bookingsApi.list()` — a
    fetchAllPages walk of every delegate row — plus a second full walk of webhook
    logs purely to count the failed ones. Measured on real data that was ~350
    sequential requests for one dashboard load (13,269 delegates, 130,287 webhook
    logs), which presented to the developer as "the backend is running in a loop".
    It is a GROUP BY; the database should do it.

    RESOLVED PAYMENT STATUS
    Buckets are computed over
        COALESCE(NULLIF(delegate_payment_status, ''), invoice.payment_status)
    the same expression accounts/filter_spec.py and book_delegate/filters.py use,
    and the same value the serializer exposes as `effective_payment_status`.
    Bucketing the raw invoice column instead would disagree with the table the
    user is looking at for any delegate carrying an override.

    THE PIPELINE SPLIT — SpEx vs Speaker Sales vs delegate sales
    There is no pipeline column. It is derived from the free-text booking code
    through book_event/booking_code.py, whose matching is BOUNDARY-ANCHORED and
    whose marker lists live in settings. This view previously inlined its own
    unanchored `icontains` copies of those predicates, which is the exact bug
    that module exists to end ("SPP" is three characters and matches inside
    "SUPPLEMENT"). Precedence is SpEx, then speaker, then delegate — matching
    classify() — so the three lines sum to the total with nothing double-counted.

    Classification reads the DELEGATE's own booking_code, not the invoice's. One
    invoice can carry delegates booked on different terms (a Speaker and a Group
    Pass together is a real combination) and BookEvent has one code to describe
    all of them; BookDelegate.save() defaults the column from the invoice, so it
    is never blank. On the 2026-06-11 snapshot both columns classify identically
    (353 SpEx / 1,577 speaker / 1,070 delegate), so this is a precision gain, not
    a change of answer.

    WHO A BOOKING BELONGS TO
    See _owner_by_event(). The short version: invoice.sales_executive is the
    authority wherever it is set, and the event catalogue is the fallback,
    because on real data that FK is NULL on every invoice while the catalogue
    does carry ownership. Reading only the FK is why every team on this dashboard
    reported 0 bookings.

    RBAC-scoped through the same _event_codes() helper as the rest of this module.
    """
    permission_classes = [IsAuthenticated]

    # Mirrors bucketOf() in frontend/src/api/reports.js. Order matters: the
    # Credit prefix test has to run before the plain equality tests.
    BUCKETS = ["paid", "pending", "free", "credit", "unpaid", "cancelled"]

    PIPELINES = ("sales", "spex", "speaker")

    # team_type -> the pipeline its members are measured on. Telemarketing sells
    # delegate seats and is attributed through the same chain as Sales, which is
    # what book_event/views.py:240 does for every non-SpEx, non-Speaker role. A
    # team whose type is absent here is not a booking team (Market Research,
    # Data Mining, Operations, Admin) and its members get no booking numbers —
    # previously they were shown a "sales" figure of 0, which read as a defect.
    TEAM_PIPELINE = {
        "sales": "sales",
        "telemarketing": "sales",
        "spex": "spex",
        "speaker_sales": "speaker",
    }

    # The Event column each pipeline's ownership falls back to, for the
    # `attribution` diagnostics block.
    PIPELINE_EVENT_FIELD = {
        "sales": "Event.sales_executive / Event.sales_team",
        "spex": "Event.spex_team",
        "speaker": "Event.speaker_sales_team",
    }

    # Team-name keywords, aligned with accounts.models.User.save(), which assigns
    # a member's ROLE from these same keywords — so a team's type and its
    # members' roles cannot disagree. Order matters ("Speaker Sales Team" also
    # contains "sales"). Consulted only for a team with NO members; where there
    # are members the dominant role decides, as book_event/views.py:313 does.
    TEAM_NAME_TYPES = (
        ("admin", "admin"),
        ("market research", "market_research"),
        ("research", "market_research"),
        ("data mining", "data_mining"),
        ("mining", "data_mining"),
        ("dmd", "data_mining"),
        ("spex", "spex"),
        ("operation", "operations"),
        ("speaker", "speaker_sales"),
        ("tele", "telemarketing"),
        ("sales", "sales"),
    )

    @staticmethod
    def _bucket(status):
        s = (status or "").strip()
        if s in ("Paid", "Paid (Transferred)"):
            return "paid"
        if s == "Free":
            return "free"
        if s.startswith("Credit"):
            return "credit"
        if s == "Unpaid":
            return "unpaid"
        if s in ("Cancelled", "Refunded"):
            return "cancelled"
        return "pending"

    @classmethod
    def _empty_line(cls):
        line = {"total": 0, "invoices": 0, "companies": 0}
        for b in cls.BUCKETS:
            line[b] = 0
        return line

    @classmethod
    def _team_type(cls, name, members):
        """
        A team's type: the dominant member role, else the name keywords.

        Ties break on role name rather than on queryset order so the same team
        does not change type between two identical requests.
        """
        roles = [m["role"] for m in members if m["role"]]
        if roles:
            return sorted(Counter(roles).items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        lowered = (name or "").lower()
        for needle, value in cls.TEAM_NAME_TYPES:
            if needle in lowered:
                return value
        return "sales"

    @staticmethod
    def _pipeline_case():
        """
        Anchored SpEx / speaker / delegate classification as a GROUP BY-able
        annotation. `booking_code` resolves to whichever model the queryset is
        over — BookDelegate and BookEvent each own a column of that name — so
        one expression serves both, and the two cannot drift apart.
        """
        from django.db.models import Case, CharField, Value, When

        from book_event.booking_code import speaker_q, spex_q

        return Case(
            When(spex_q("booking_code"), then=Value("spex")),
            When(speaker_q("booking_code"), then=Value("speaker")),
            default=Value("sales"),
            output_field=CharField(),
        )

    @classmethod
    def _owner_by_event(cls, users):
        """
        {pipeline: {event_code: user_id}} — who owns each pipeline on each event.

        WHY THE EVENT CATALOGUE AND NOT JUST THE INVOICE
        invoice.sales_executive is the authoritative owner wherever it is set,
        and it is preferred per row in get(). On the 2026-06-11 snapshot it is
        NULL on all 2,230 invoices, so a dashboard reading only that column
        reports 0 bookings for every member of every team — which is exactly the
        "sales team and SpEx team data is not visible" symptom. The Event
        catalogue does carry ownership (193 of 217 events name a sales
        executive), so it is the fallback, per pipeline:
            sales / telemarketing   Event.sales_executive, else Event.sales_team
            spex                    Event.spex_team
            speaker                 Event.speaker_sales_team
        the same fields book_event/views.py:212-238 attributes from.

        FREE TEXT RESOLVES EXACT-ONLY
        The team columns are CharFields holding a person's name. They resolve
        through accounts.user_resolution.UserResolver — email, then username,
        then full name, all iexact, with ambiguity reported rather than settled
        by `.first()`. Never `icontains`: events/models.py:118 matches
        sales_team that way and user_resolution.py documents at length how that
        silently attributes a booking to the wrong person.

        Returns (owner_map, diagnostics). Diagnostics carry the resolution rate
        so an unpopulated column is reportable as "no attribution data" instead
        of surfacing as a plausible-looking zero.
        """
        from accounts.user_resolution import UserResolver

        resolver = UserResolver(users)
        owner = {p: {} for p in cls.PIPELINES}
        counts = {p: 0 for p in cls.PIPELINES}
        from_fk = 0

        rows = Event.objects.values(
            "event_code", "sales_executive_id",
            "sales_team", "spex_team", "speaker_sales_team",
        )
        for ev in rows:
            code = ev["event_code"]
            if ev["sales_executive_id"]:
                owner["sales"][code] = ev["sales_executive_id"]
                counts["sales"] += 1
                from_fk += 1
            else:
                user, _ = resolver.resolve(ev["sales_team"])
                if user is not None:
                    owner["sales"][code] = user.id
                    counts["sales"] += 1
            for pipeline, field in (("spex", "spex_team"),
                                    ("speaker", "speaker_sales_team")):
                user, _ = resolver.resolve(ev[field])
                if user is not None:
                    owner[pipeline][code] = user.id
                    counts[pipeline] += 1

        diagnostics = {
            p: {
                "source": cls.PIPELINE_EVENT_FIELD[p],
                "events_mapped": counts[p],
                "available": bool(counts[p]),
            }
            for p in cls.PIPELINES
        }
        diagnostics["events_total"] = Event.objects.count()
        diagnostics["events_from_sales_executive_fk"] = from_fk
        diagnostics["name_resolution"] = resolver.report(limit=10)
        return owner, diagnostics

    def get(self, request):
        from django.contrib.auth import get_user_model
        from django.db.models import Value
        from django.db.models.functions import NullIf
        from teams.models import Team
        from ticket_central.models import Ticket

        # One resolver for every screen that offers these presets, so the
        # Dashboard and the Bookings table cannot disagree about where a window
        # starts. See accounts/period_filter.py for the rolling-window rule and
        # for why "today" is the UTC date.
        try:
            period, p_from, p_to = period_filter.resolve_period(
                request.query_params.get("period"))
        except period_filter.PeriodError as exc:
            return Response({"detail": str(exc)}, status=400)

        codes = _event_codes(request.user)
        del_qs = BookDelegate.objects.all()
        inv_qs = BookEvent.objects.all()
        if codes is not None:
            del_qs = del_qs.filter(event_code__in=codes)
            inv_qs = inv_qs.filter(event_code__in=codes)

        # The resolved person-level status, as an annotation we can GROUP BY.
        del_qs = del_qs.annotate(
            _status=Coalesce(
                NullIf("delegate_payment_status", Value("")),
                "invoice__payment_status",
            ),
        )

        # ── Period ───────────────────────────────────────────────────────────
        # Filtered on the same date the monthly chart is keyed by —
        # COALESCE(request_date, invoice_date) — so a bar in the chart and a
        # number in the cards can never come from two different definitions of
        # when a booking happened. `alias` rather than `annotate`: the window is
        # a WHERE clause, and an annotation would join the GROUP BY below.
        #
        # A booking with neither date cannot be placed in time and is therefore
        # OUTSIDE every window but inside "all". That is a real (if currently
        # empty — request_date is set on all 2,230 invoices) difference, so the
        # response reports the count rather than letting the rows go missing
        # silently.
        booked_on = Coalesce("invoice__request_date", "invoice__invoice_date")
        undated = del_qs.alias(_booked=booked_on).filter(_booked=None).count()

        # ── Outstanding work, ALWAYS all-time ────────────────────────────────
        # The dashboard's action queue ("bookings awaiting payment", "unpaid
        # invoices past due") is a WORKLIST, not an analytic. Scoping it to the
        # selected window would have shown "0 unpaid" under Last 7 days while 287
        # bookings sat unpaid — a filter turning a backlog invisible is worse than
        # no filter. Published separately so the same response can carry both
        # readings and the UI never has to choose between them.
        outstanding = {b: 0 for b in self.BUCKETS}
        outstanding["total"] = 0
        for r in del_qs.values("_status").annotate(n=Count("id")):
            outstanding[self._bucket(r["_status"])] += r["n"]
            outstanding["total"] += r["n"]

        if p_from is not None:
            del_qs = (del_qs.alias(_booked=booked_on)
                      .filter(_booked__gte=p_from, _booked__lte=p_to))
            inv_qs = (inv_qs.alias(_booked=Coalesce("request_date", "invoice_date"))
                      .filter(_booked__gte=p_from, _booked__lte=p_to))

        User = get_user_model()
        users = list(User.objects.filter(is_active=True).values(
            "id", "first_name", "last_name", "username", "email", "role",
            "is_team_lead", "team_id"))
        owner, attribution = self._owner_by_event(
            User.objects.filter(is_active=True)
        )

        # ── Pipelines, buckets and per-user attribution, in ONE GROUP BY ─────
        # Grouped by (pipeline, status, event_code, invoice sales executive):
        # the payment-mix lines are a roll-up of the first two columns, and
        # attribution needs the last two. At most one group per delegate row.
        lines = {"all": self._empty_line()}
        for p in self.PIPELINES:
            lines[p] = self._empty_line()
        per_user = {}       # (user_id, pipeline) -> {bookings, paid}
        pipe = {p: {"total": 0, "paid": 0, "attributed": 0} for p in self.PIPELINES}

        for r in (del_qs.annotate(_pipeline=self._pipeline_case())
                  .values("_pipeline", "_status", "event_code",
                          "invoice__sales_executive_id")
                  .annotate(n=Count("id"))):
            pipeline = r["_pipeline"]
            bucket = self._bucket(r["_status"])
            n = r["n"]
            for target in ("all", pipeline):
                lines[target]["total"] += n
                lines[target][bucket] += n

            stats = pipe[pipeline]
            stats["total"] += n
            if bucket == "paid":
                stats["paid"] += n

            uid = (r["invoice__sales_executive_id"]
                   or owner[pipeline].get(r["event_code"]))
            if uid:
                u = per_user.setdefault((uid, pipeline), {"bookings": 0, "paid": 0})
                u["bookings"] += n
                stats["attributed"] += n
                if bucket == "paid":
                    u["paid"] += n

        # ── Invoice and company counts per pipeline ──────────────────────────
        # SpEx sells sponsorship packages to COMPANIES, not seats to people, so
        # the number the SpEx team is measured on is distinct companies on SpEx
        # invoices — the measure book_event/views.py:257 already reports. The
        # delegate rows on a SpEx invoice are the passes bundled with the
        # package, which is why the delegate `total` above is not that number.
        # Counted through sets rather than by counting GROUP BY rows: company_name
        # is free text, so "Acme" and "Acme " are two groups and one company, and
        # a BLANK is an invoice with no company recorded rather than a company at
        # all. Counting rows would report 132 sponsors where there are 131 and a
        # gap. Bounded by the invoice count, so it stays a set of thousands.
        seen = {p: set() for p in self.PIPELINES}
        for r in (inv_qs.annotate(_pipeline=self._pipeline_case())
                  .values("_pipeline", "company_name")
                  .annotate(n=Count("id"))):
            lines[r["_pipeline"]]["invoices"] += r["n"]
            lines["all"]["invoices"] += r["n"]
            company = (r["company_name"] or "").strip().casefold()
            if company:
                seen[r["_pipeline"]].add(company)
        for p in self.PIPELINES:
            lines[p]["companies"] = len(seen[p])
        # NOT the sum of the per-pipeline counts: one company can sponsor AND send
        # delegates, and would otherwise be counted twice.
        lines["all"]["companies"] = len(set().union(*seen.values()))

        # ── Bookings by month, overall and per pipeline ──────────────────────
        # Keyed YYYY-MM off request_date, falling back to invoice_date, matching
        # what the browser-side version did. Formatted in Python rather than SQL
        # so the result does not depend on the backend's date-formatting dialect.
        months = {}
        pipe_months = {p: {} for p in self.PIPELINES}
        for r in (del_qs
                  .annotate(_d=booked_on, _pipeline=self._pipeline_case())
                  .exclude(_d=None)
                  .values("_d", "_status", "_pipeline")
                  .annotate(n=Count("id"))):
            key = r["_d"].strftime("%Y-%m")
            m = months.setdefault(key, {"label": key, "total": 0, "paid": 0, "pending": 0, "free": 0})
            bucket = self._bucket(r["_status"])
            m["total"] += r["n"]
            if bucket in ("paid", "pending", "free"):
                m[bucket] += r["n"]
            pm = pipe_months[r["_pipeline"]]
            pm[key] = pm.get(key, 0) + r["n"]
        months = sorted(months.values(), key=lambda x: x["label"])
        # Last 6 months of each pipeline, for the per-team sparkline. Taken from
        # the month keys actually present so a gap does not shift the line.
        trend = {
            p: [pipe_months[p][k] for k in sorted(pipe_months[p])][-6:]
            for p in self.PIPELINES
        }

        # ── Channel mix ──────────────────────────────────────────────────────
        total_delegates = lines["all"]["total"]
        CHANNEL_LABEL = {"manual": "Manual", "website": "Website"}
        channels = []
        for r in del_qs.values("invoice__source").annotate(n=Count("id")):
            src = r["invoice__source"] or "manual"
            channels.append({
                "n": CHANNEL_LABEL.get(src, src),
                "p": round(r["n"] * 100 / total_delegates) if total_delegates else 0,
            })

        def display_name(u):
            full = (u["first_name"] + " " + u["last_name"]).strip()
            return full or u["username"]

        # Ticket aggregates per user, for the market-research / data-mining tiles.
        # Previously derived by walking all 35,690 tickets in the browser.
        #
        # NOT period-filtered, and the response says so (`period.applies_to`).
        # A ticket's dates are its own (assign_date / complete_date) and are
        # unrelated to when a booking was raised; filtering them by the booking
        # window would answer a question nobody asked. The tickets table is empty
        # on this snapshot, so there is no observable behaviour to verify a
        # window against either.
        mined_by_name = {}
        for r in (Ticket.objects.filter(status="completed")
                  .exclude(assign_name="")
                  .values("assign_name")
                  .annotate(n=Coalesce(Sum("mined_count"), 0))):
            mined_by_name[r["assign_name"]] = r["n"]
        # assigned_mr stores an EMAIL ADDRESS, not a display name. Keying this by
        # display name produced 0 matches for every user and every team — sampled
        # against the live data: 9 distinct assigned_mr values, all of the form
        # "vick.varela@iq-hub.com", against 45 users whose display names are
        # "Vick Varela". So the join key is email, lowercased on both sides
        # because the column is free text.
        raised_by_email = {}
        for r in (Ticket.objects.exclude(assigned_mr="")
                  .values("assigned_mr").annotate(n=Count("id"))):
            raised_by_email[(r["assigned_mr"] or "").strip().lower()] = r["n"]
        # assign_name is NOT resolvable to a user and deliberately left name-keyed.
        # Sampled live: 485 distinct values, ZERO matching any username or display
        # name. They are free text from spreadsheet imports and include people who
        # are not CRM users at all (an offshore mining team), multi-person entries
        # ("SC - Khushbu Vaidya / Krishna Prajapati"), a "SC - " prefix convention,
        # and mis-mapped column garbage ("00", "17-Dec", "3-Feb-2026"). No matching
        # rule can fix that; it needs a real FK or an explicit mapping table.
        # Consequence: per-team `mined` stays 0. Reported rather than faked.
        worked_by_name = {}
        for r in (Ticket.objects.exclude(assign_name="")
                  .values("assign_name").annotate(n=Count("id"))):
            worked_by_name[r["assign_name"]] = r["n"]

        NO_BOOKINGS = {"bookings": 0, "paid": 0}
        booking_team_productivity = []
        for t in Team.objects.filter(is_archived=False):
            team_users = [x for x in users if x["team_id"] == t.id]
            team_type = self._team_type(t.name, team_users)
            pipeline = self.TEAM_PIPELINE.get(team_type)

            members = []
            for u in team_users:
                stat = (per_user.get((u["id"], pipeline), NO_BOOKINGS)
                        if pipeline else NO_BOOKINGS)
                name = display_name(u)
                members.append({
                    "user_id": u["id"], "name": name, "role": u["role"],
                    "is_lead": bool(u["is_team_lead"]),
                    "bookings": stat["bookings"], "paid": stat["paid"],
                    "conv": round(stat["paid"] * 100 / stat["bookings"]) if stat["bookings"] else 0,
                    "mined": mined_by_name.get(name, 0),
                    "raised": raised_by_email.get((u["email"] or "").strip().lower(), 0),
                    "worked": worked_by_name.get(name, 0),
                })
            tb = sum(m["bookings"] for m in members)
            tp = sum(m["paid"] for m in members)
            stats = pipe.get(pipeline) if pipeline else None
            booking_team_productivity.append({
                "team_id": t.id, "team_name": t.name, "color": t.color,
                "team_type": team_type, "members": members,
                "bookings": tb,
                "conv": round(tp * 100 / tb) if tb else 0,
                "paid": tp,
                "trend": trend.get(pipeline, []) if pipeline else [],
                "mined": sum(m["mined"] for m in members),
                "raised": sum(m["raised"] for m in members),
                # PIPELINE-wide, not team-wide: every record in this team's
                # pipeline for the window, whoever it belongs to. It lets the UI
                # distinguish "this team's pipeline is empty" from "the pipeline
                # has 353 records and none of them name a member" — the two look
                # identical if only `bookings` is published, and the second is
                # what an unpopulated Event.spex_team produces.
                "pipeline": pipeline or "",
                "pipeline_total": stats["total"] if stats else 0,
                "pipeline_paid": stats["paid"] if stats else 0,
                "pipeline_unattributed": (stats["total"] - stats["attributed"]) if stats else 0,
                "pipeline_invoices": lines[pipeline]["invoices"] if pipeline else 0,
                "pipeline_companies": lines[pipeline]["companies"] if pipeline else 0,
                "attribution_source": self.PIPELINE_EVENT_FIELD.get(pipeline, ""),
                "attribution_available": bool(pipeline and owner[pipeline]),
            })

        # Flat per-display-name totals. Teams Management keys on the display
        # name, and previously built these two maps by walking every delegate and
        # every ticket in the browser. Summed across pipelines, not scoped to one:
        # this answers "how much has this person booked", and a person's team
        # does not bound that.
        by_name = {}
        for u in users:
            name = display_name(u)
            bookings = sum(per_user.get((u["id"], p), NO_BOOKINGS)["bookings"]
                           for p in self.PIPELINES)
            paid = sum(per_user.get((u["id"], p), NO_BOOKINGS)["paid"]
                       for p in self.PIPELINES)
            by_name[name] = {
                "bookings": bookings, "paid": paid,
                "tickets": (raised_by_email.get((u["email"] or "").strip().lower(), 0)
                            + worked_by_name.get(name, 0)),
            }

        return Response({
            "period": {
                "key": period,
                "from": p_from.isoformat() if p_from else None,
                "to": p_to.isoformat() if p_to else None,
                "days": PERIOD_DAYS[period],
                "date_field": "COALESCE(request_date, invoice_date)",
                "undated_records": undated,
                "applies_to": ["pipelines", "months", "channels",
                               "team bookings", "companies"],
                "excludes": ["outstanding", "tickets", "webhook failures"],
            },
            # All-time regardless of `period` — see the note at its computation.
            "outstanding": outstanding,
            "all": lines["all"], "sales": lines["sales"],
            "spex": lines["spex"], "speaker": lines["speaker"],
            "months": months, "channels": channels,
            "booking_team_productivity": booking_team_productivity,
            "per_user_by_name": by_name,
            "attribution": {
                **attribution,
                "invoices_with_sales_executive":
                    inv_qs.exclude(sales_executive=None).count(),
                "unattributed_delegates": sum(
                    pipe[p]["total"] - pipe[p]["attributed"] for p in self.PIPELINES
                ),
            },
        })
