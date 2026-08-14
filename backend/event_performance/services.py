"""
Event Performance metrics engine.
All metrics are computed live from BookEvent / BookDelegate / Event data.
No denormalization — every number derives from the source of truth.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Sum, Q, F, DecimalField, Value
from django.db.models.functions import Coalesce

from book_event.models import BookEvent
from book_delegate.models import BookDelegate

PAID_STATUSES    = ["Paid"]
PENDING_STATUSES = ["Pending"]
FREE_STATUSES    = ["Free"]
CANCELLED_STATUSES = ["Cancelled", "Refunded"]

def bulk_event_metrics(event_codes: list[str]) -> dict:
    """
    Returns a dict keyed by event_code.
    Each event code's metrics are calculated from BookEvent / BookDelegate records
    where event_code matches exactly and edition matches the Event's edition year.
    """
    if not event_codes:
        return {}

    from events.models import Event
    from django.db.models import Q
    from decimal import Decimal

    events_list = Event.objects.filter(event_code__in=event_codes)
    event_year_map = {}
    for e in events_list:
        if e.event_date:
            event_year_map[e.event_code] = e.event_date.year

    booking_filter = Q()
    delegate_filter = Q()
    for ec in event_codes:
        yr = event_year_map.get(ec)
        if yr is not None:
            booking_filter |= Q(event_code=ec, edition=yr)
            delegate_filter |= Q(invoice__event_code=ec, invoice__edition=yr)
        else:
            booking_filter |= Q(event_code=ec)
            delegate_filter |= Q(invoice__event_code=ec)

    today     = date.today()
    yesterday = today - timedelta(days=1)
    d7_start  = today - timedelta(days=7)
    d14_start = today - timedelta(days=14)
    d21_start = today - timedelta(days=21)

    # Query 1: Revenue from invoices
    revenue_qs = (
        BookEvent.objects
        .filter(booking_filter)
        .values("event_code", "edition")
        .annotate(
            total_invoices   = Count("id"),
            total_revenue    = Coalesce(Sum("total_amount", filter=Q(payment_status__in=PAID_STATUSES)),    Value(Decimal("0")), output_field=DecimalField()),
            pending_value    = Coalesce(Sum("total_amount", filter=Q(payment_status__in=PENDING_STATUSES)), Value(Decimal("0")), output_field=DecimalField()),
            today_revenue    = Coalesce(Sum("total_amount", filter=Q(payment_status__in=PAID_STATUSES, payment_date=today)),                                   Value(Decimal("0")), output_field=DecimalField()),
            yesterday_revenue= Coalesce(Sum("total_amount", filter=Q(payment_status__in=PAID_STATUSES, payment_date=yesterday)),                               Value(Decimal("0")), output_field=DecimalField()),
            d7_revenue       = Coalesce(Sum("total_amount", filter=Q(payment_status__in=PAID_STATUSES, payment_date__gte=d7_start,  payment_date__lte=today)), Value(Decimal("0")), output_field=DecimalField()),
            d14_revenue      = Coalesce(Sum("total_amount", filter=Q(payment_status__in=PAID_STATUSES, payment_date__gte=d14_start, payment_date__lte=today)), Value(Decimal("0")), output_field=DecimalField()),
            d21_revenue      = Coalesce(Sum("total_amount", filter=Q(payment_status__in=PAID_STATUSES, payment_date__gte=d21_start, payment_date__lte=today)), Value(Decimal("0")), output_field=DecimalField()),
        )
    )

    # Query 2: All headcounts from delegates
    delegate_qs = (
        BookDelegate.objects
        .filter(delegate_filter)
        .values(base_code=F("invoice__event_code"), base_edition=F("invoice__edition"))
        .annotate(
            total_delegates     = Count("id"),
            paid_count          = Count("id", filter=Q(invoice__payment_status__in=PAID_STATUSES)),
            pending_count       = Count("id", filter=Q(invoice__payment_status__in=PENDING_STATUSES)),
            free_count          = Count("id", filter=Q(invoice__payment_status__in=FREE_STATUSES)),
            cancelled_count     = Count("id", filter=Q(invoice__payment_status__in=CANCELLED_STATUSES)),
            confirmed_delegates = Count("id", filter=Q(attendance="Confirmed")),
            noshow_delegates    = Count("id", filter=Q(attendance="No-show")),
            vip_count           = Count("id", filter=Q(invoice__ticket_tier="VIP")),
            speaker_count       = Count("id", filter=Q(invoice__ticket_tier="Speaker")),
            sponsor_count       = Count("id", filter=Q(invoice__ticket_tier="Sponsor")),
            complimentary_count = Count("id", filter=Q(invoice__ticket_tier="Complimentary")),
            paid_pof_count      = Count("id", filter=Q(invoice__paid_or_free="Paid")),
            free_pof_count      = Count("id", filter=Q(invoice__paid_or_free="Free")),
            today_paid     = Count("id", filter=Q(invoice__payment_status__in=PAID_STATUSES, invoice__payment_date=today)),
            yesterday_paid = Count("id", filter=Q(invoice__payment_status__in=PAID_STATUSES, invoice__payment_date=yesterday)),
            d7_paid        = Count("id", filter=Q(invoice__payment_status__in=PAID_STATUSES, invoice__payment_date__gte=d7_start,  invoice__payment_date__lte=today)),
            d14_paid       = Count("id", filter=Q(invoice__payment_status__in=PAID_STATUSES, invoice__payment_date__gte=d14_start, invoice__payment_date__lte=today)),
            d21_paid       = Count("id", filter=Q(invoice__payment_status__in=PAID_STATUSES, invoice__payment_date__gte=d21_start, invoice__payment_date__lte=today)),
        )
    )

    revenue_map = {
        (row["event_code"], row["edition"]): row
        for row in revenue_qs
    }
    delegate_map = {
        (row["base_code"], row["base_edition"]): row
        for row in delegate_qs
    }

    result = {}
    for ec in event_codes:
        yr = event_year_map.get(ec)
        r = revenue_map.get((ec, yr), {})
        d = delegate_map.get((ec, yr), {})

        result[ec] = {
            "total_delegates":     d.get("total_delegates",     0) or 0,
            "paid_count":          d.get("paid_count",          0) or 0,
            "pending_count":       d.get("pending_count",       0) or 0,
            "free_count":          d.get("free_count",          0) or 0,
            "cancelled_count":     d.get("cancelled_count",     0) or 0,
            "confirmed_delegates": d.get("confirmed_delegates", 0) or 0,
            "noshow_delegates":    d.get("noshow_delegates",    0) or 0,
            "vip_count":           d.get("vip_count",           0) or 0,
            "speaker_count":       d.get("speaker_count",       0) or 0,
            "sponsor_count":       d.get("sponsor_count",       0) or 0,
            "complimentary_count": d.get("complimentary_count", 0) or 0,
            "paid_pof_count":      d.get("paid_pof_count",      0) or 0,
            "free_pof_count":      d.get("free_pof_count",      0) or 0,
            "today_paid":          d.get("today_paid",    0) or 0,
            "yesterday_paid":      d.get("yesterday_paid",0) or 0,
            "d7_paid":             d.get("d7_paid",       0) or 0,
            "d14_paid":            d.get("d14_paid",      0) or 0,
            "d21_paid":            d.get("d21_paid",      0) or 0,
            "total_revenue":       float(r.get("total_revenue",    0) or 0),
            "pending_value":       float(r.get("pending_value",    0) or 0),
            "today_revenue":       float(r.get("today_revenue",    0) or 0),
            "yesterday_revenue":   float(r.get("yesterday_revenue",0) or 0),
            "d7_revenue":          float(r.get("d7_revenue",       0) or 0),
            "d14_revenue":         float(r.get("d14_revenue",      0) or 0),
            "d21_revenue":         float(r.get("d21_revenue",      0) or 0),
            "total_invoices":      r.get("total_invoices", 0) or 0,
        }
    return result


def compute_health(paid_count: int, capacity: int) -> dict:
    """
    Returns benchmark %, priority score, and health colour.
    benchmark = paid_count / capacity * 100
    """
    if not capacity:
        return {"benchmark": 0.0, "health": "unknown", "color": "grey"}

    benchmark = round((paid_count / capacity) * 100, 1)

    if benchmark >= 75:
        health, color = "healthy",  "green"
    elif benchmark >= 50:
        health, color = "on_track", "blue"
    elif benchmark >= 25:
        health, color = "warning",  "amber"
    else:
        health, color = "critical", "red"

    return {"benchmark": benchmark, "health": health, "color": color}


def reps_performance(event_codes: list[str]) -> list[dict]:
    """
    Per-rep breakdown: paid bookings + revenue + pending for a set of events.
    """
    if not event_codes:
        return []

    from events.models import Event
    from django.db.models import Q
    from decimal import Decimal

    events_list = Event.objects.filter(event_code__in=event_codes)
    event_year_map = {}
    for e in events_list:
        if e.event_date:
            event_year_map[e.event_code] = e.event_date.year

    booking_filter = Q()
    for ec in event_codes:
        yr = event_year_map.get(ec)
        if yr is not None:
            booking_filter |= Q(event_code=ec, edition=yr)
        else:
            booking_filter |= Q(event_code=ec)

    qs = (
        BookEvent.objects
        .filter(booking_filter)
        .values(
            rep_id        = F("sales_executive__id"),
            rep_first     = F("sales_executive__first_name"),
            rep_last      = F("sales_executive__last_name"),
            rep_username  = F("sales_executive__username"),
        )
        .annotate(
            paid_bookings    = Count("id", filter=Q(payment_status__in=PAID_STATUSES)),
            pending_bookings = Count("id", filter=Q(payment_status__in=PENDING_STATUSES)),
            total_revenue    = Coalesce(Sum("total_amount", filter=Q(payment_status__in=PAID_STATUSES)),    Value(Decimal("0")), output_field=DecimalField()),
            pending_value    = Coalesce(Sum("total_amount", filter=Q(payment_status__in=PENDING_STATUSES)), Value(Decimal("0")), output_field=DecimalField()),
        )
        .order_by("-paid_bookings")
    )
    rows = []
    for r in qs:
        first = r["rep_first"] or ""
        last  = r["rep_last"]  or ""
        full  = (first + " " + last).strip() or r["rep_username"] or "Unassigned"
        rows.append({
            "rep_id":           r["rep_id"],
            "rep_name":         full,
            "paid_bookings":    r["paid_bookings"],
            "pending_bookings": r["pending_bookings"],
            "total_revenue":    float(r["total_revenue"] or 0),
            "pending_value":    float(r["pending_value"]  or 0),
        })
    return rows
