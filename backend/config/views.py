"""
config/views.py
────────────────
Global search + dashboard stats — RBAC-scoped per user role.
"""
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from book_delegate.models import BookDelegate
from book_delegate.serializers import BookDelegateListSerializer
from book_event.models import BookEvent
from book_event.serializers import BookEventListSerializer
from companies.models import Company
from companies.serializers import CompanySerializer
from events.models import Event
from events.serializers import EventListSerializer


def _event_codes(user):
    """None = unrestricted (admin). List = allowed codes (sales)."""
    if user.is_admin:
        return None
    return user.assigned_event_codes() or []


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
    GET /api/stats/dashboard_aggregate/

    Everything the Dashboard needs that is a GROUP BY, computed in SQL.

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

    RBAC-scoped through the same _event_codes() helper as the rest of this module.
    """
    permission_classes = [IsAuthenticated]

    # Mirrors bucketOf() in frontend/src/api/reports.js. Order matters: the
    # Credit prefix test has to run before the plain equality tests.
    BUCKETS = ["paid", "pending", "free", "credit", "unpaid", "cancelled"]

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
        line = {"total": 0}
        for b in cls.BUCKETS:
            line[b] = 0
        return line

    def get(self, request):
        from django.contrib.auth import get_user_model
        from django.db.models import Case, CharField, Value, When
        from django.db.models.functions import NullIf
        from teams.models import Team
        from ticket_central.models import Ticket

        codes = _event_codes(request.user)
        del_qs = BookDelegate.objects.all()
        if codes is not None:
            del_qs = del_qs.filter(event_code__in=codes)

        # The resolved person-level status, as an annotation we can GROUP BY.
        del_qs = del_qs.annotate(
            _status=Coalesce(
                NullIf("delegate_payment_status", Value("")),
                "invoice__payment_status",
            ),
        )

        # Pipeline comes from invoice.booking_code, the same heuristic as
        # book_event/views.py SPEX_Q / SPEAKER_Q. There is no pipeline field.
        SPEX_Q = Q(invoice__booking_code__icontains="spex") | Q(invoice__booking_code__iexact="Add-Ons")
        SPEAKER_Q = Q(invoice__booking_code__icontains="speaker") | Q(invoice__booking_code__icontains="spp")
        pipeline_case = Case(
            When(SPEX_Q, then=Value("spex")),
            When(SPEAKER_Q, then=Value("speaker")),
            default=Value("sales"),
            output_field=CharField(),
        )

        lines = {
            "all": self._empty_line(), "sales": self._empty_line(),
            "spex": self._empty_line(), "speaker": self._empty_line(),
        }
        for r in (del_qs.annotate(_pipeline=pipeline_case)
                  .values("_status", "_pipeline")
                  .annotate(n=Count("id"))):
            bucket = self._bucket(r["_status"])
            n = r["n"]
            for target in ("all", r["_pipeline"]):
                lines[target]["total"] += n
                lines[target][bucket] += n

        # ── Bookings by month ────────────────────────────────────────────────
        # Keyed YYYY-MM off request_date, falling back to invoice_date, matching
        # what the browser-side version did. Formatted in Python rather than SQL
        # so the result does not depend on the backend's date-formatting dialect.
        months = {}
        for r in (del_qs
                  .annotate(_d=Coalesce("invoice__request_date", "invoice__invoice_date"))
                  .exclude(_d=None)
                  .values("_d", "_status")
                  .annotate(n=Count("id"))):
            key = r["_d"].strftime("%Y-%m")
            m = months.setdefault(key, {"label": key, "total": 0, "paid": 0, "pending": 0, "free": 0})
            bucket = self._bucket(r["_status"])
            m["total"] += r["n"]
            if bucket in ("paid", "pending", "free"):
                m[bucket] += r["n"]
        months = sorted(months.values(), key=lambda x: x["label"])

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

        # ── Per-user booking attribution ─────────────────────────────────────
        # Via invoice.sales_executive, one GROUP BY rather than a browser-side
        # tally keyed on a display name.
        per_user = {}
        for r in (del_qs
                  .exclude(invoice__sales_executive=None)
                  .values("invoice__sales_executive_id", "_status")
                  .annotate(n=Count("id"))):
            u = per_user.setdefault(r["invoice__sales_executive_id"], {"bookings": 0, "paid": 0})
            u["bookings"] += r["n"]
            if self._bucket(r["_status"]) == "paid":
                u["paid"] += r["n"]

        User = get_user_model()
        users = list(User.objects.filter(is_active=True).values(
            "id", "first_name", "last_name", "username", "email", "role",
            "is_team_lead", "team_id"))

        def display_name(u):
            full = (u["first_name"] + " " + u["last_name"]).strip()
            return full or u["username"]

        def team_type_of(name):
            n = (name or "").lower()
            for needle, value in (
                ("spex", "spex"), ("speaker", "speaker_sales"), ("mining", "data_mining"),
                ("research", "market_research"), ("tele", "telemarketing"),
                ("operations", "operations"),
            ):
                if needle in n:
                    return value
            return "sales"

        # Ticket aggregates per user, for the market-research / data-mining tiles.
        # Previously derived by walking all 35,690 tickets in the browser.
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

        booking_team_productivity = []
        for t in Team.objects.filter(is_archived=False):
            members = []
            for u in [x for x in users if x["team_id"] == t.id]:
                stat = per_user.get(u["id"], {"bookings": 0, "paid": 0})
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
            booking_team_productivity.append({
                "team_id": t.id, "team_name": t.name, "color": t.color,
                "team_type": team_type_of(t.name), "members": members,
                "bookings": tb,
                "conv": round(tp * 100 / tb) if tb else 0,
                "trend": [],
                "mined": sum(m["mined"] for m in members),
                "raised": sum(m["raised"] for m in members),
            })

        # Flat per-display-name totals. Teams Management keys on the display
        # name, and previously built these two maps by walking every delegate and
        # every ticket in the browser.
        by_name = {}
        for t in booking_team_productivity:
            for m in t["members"]:
                by_name[m["name"]] = {
                    "bookings": m["bookings"], "paid": m["paid"],
                    "tickets": m["raised"] + m["worked"],
                }

        return Response({
            "all": lines["all"], "sales": lines["sales"],
            "spex": lines["spex"], "speaker": lines["speaker"],
            "months": months, "channels": channels,
            "booking_team_productivity": booking_team_productivity,
            "per_user_by_name": by_name,
        })
