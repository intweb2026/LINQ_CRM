from __future__ import annotations

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Q

from accounts.permissions import IsAdminRole
from events.models import Event
from .queries import event_payment_metrics, event_paid_bookings
from .calculator import calc_trend, calc_activity_color
from .serializers import EventPaymentActivitySerializer, PaidBookingSerializer


def _rep_name(user) -> str:
    if not user:
        return "—"
    full = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return full or user.username or "—"


def _build_row(event: Event, metrics: dict) -> dict:
    m          = metrics.get(event.event_code, {})
    paid_7d    = m.get("paid_7d", 0) or 0
    prev_7d    = m.get("prev_7d", 0) or 0
    trend, trend_color = calc_trend(paid_7d, prev_7d)

    return {
        "event_code":        event.event_code,
        "event_name":        event.name,
        "event_date":        event.event_date,
        "status":            event.status,
        "city":              event.city,
        "sales_rep":         _rep_name(event.sales_executive),

        "total_paid":        m.get("total_paid", 0) or 0,
        "paid_7d":           paid_7d,
        "paid_15d":          m.get("paid_15d", 0) or 0,
        "paid_30d":          m.get("paid_30d", 0) or 0,
        "prev_7d":           prev_7d,

        "last_payment_date": m.get("last_payment_date"),
        "last_booking_date": m.get("last_booking_date"),

        "trend":             trend,
        "trend_color":       trend_color,
        "activity_color":    calc_activity_color(paid_7d),
    }


class PaymentActivityViewSet(viewsets.ViewSet):
    # IsAdminRole, not IsAuthenticated. This viewset is mounted INSIDE the
    # event-performance router (see ../urls.py) and answers with the same
    # commercially sensitive figures the sibling EventPerformanceViewSet
    # guards as admin-only: per-event paid-booking counts, rolling 7/15/30-day
    # payment totals and the named sales rep on each event. IsAuthenticated
    # made all of that readable by any logged-in session's token, so the
    # restricted page had an unrestricted door on the same mount point.
    #
    # The two must stay in step: whatever gates /api/event-performance/ gates
    # this. The frontend asks the matching question via `isAdmin`
    # (frontend/src/context/SessionContext.jsx) rather than the `performance`
    # module, which no longer opens either endpoint.
    permission_classes = [IsAdminRole]

    def list(self, request):
        """
        GET /api/event-performance/payment-activity/
        All events with payment activity metrics.
        Supports: ?status= ?sub_company= ?search=
        """
        qs = Event.objects.select_related("sales_executive").order_by("-event_date")

        status_f      = request.query_params.get("status")
        search        = request.query_params.get("search", "").strip()

        if status_f:
            qs = qs.filter(status=status_f)
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(event_code__icontains=search))

        events      = list(qs)
        event_codes = [e.event_code for e in events]
        metrics     = event_payment_metrics(event_codes)

        rows       = [_build_row(e, metrics) for e in events]
        serializer = EventPaymentActivitySerializer(rows, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="bookings")
    def bookings(self, request, pk=None):
        """
        GET /api/event-performance/payment-activity/{event_code}/bookings/
        All paid invoices for one event.
        Supports: ?days=7|15|30  (omit for all)
        """
        days_raw = request.query_params.get("days")
        days     = int(days_raw) if days_raw and days_raw.isdigit() else None

        qs   = event_paid_bookings(pk, days)
        rows = []
        for bk in qs:
            rows.append({
                "invoice_number": bk.invoice_number,
                "company_name":   bk.company_name or "—",
                "contact_name":   bk.contact_name or "—",
                "contact_email":  bk.contact_email or "—",
                "payment_type":   bk.payment_type or "—",
                "payment_status": bk.payment_status,
                "payment_date":   bk.payment_date,
                "total_amount":   float(bk.total_amount) if bk.total_amount is not None else None,
                "currency":       bk.currency,
                "delegate_count": bk.delegate_count,
                "created_at":     bk.created_at,
                "sales_rep":      _rep_name(bk.sales_executive),
            })

        serializer = PaidBookingSerializer(rows, many=True)
        return Response(serializer.data)
