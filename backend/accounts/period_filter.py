"""
accounts/period_filter.py
──────────────────────────
One date-range vocabulary for every list endpoint and aggregate in the CRM.

WHY THIS IS SHARED CODE AND NOT FOUR COPIES
The Dashboard, Bookings, Ticket Central, Paper Review and Proposal Submission all
grew a "last 7 days / last 30 days" control. Five implementations of "the last 30
days" is five chances to disagree — and they do not disagree loudly, they disagree
by a day at a boundary, which nobody notices until two screens are compared and
one of them is wrong. The keys, the arithmetic and the inclusivity rule live here
once.

WHY `?period=` AND NOT A filter_spec CRITERION
filter_spec already offers `between` on any date column, and for a user picking
arbitrary dates that remains the right tool. This is for the fixed presets, and
they need two things filter_spec deliberately does not give:

  * A COALESCED date. A booking is dated by request_date, falling back to
    invoice_date — the expression the dashboard's monthly chart is keyed on. A
    filter_spec criterion names ONE column, so a range on request_date alone
    would quietly drop the 85 invoices that carry only an invoice_date, and the
    Bookings table would then disagree with the Dashboard for the same window.
  * created_at. Ticket Central's chronological column is "Added Time", and
    accounts/filter_spec.py DEFAULT_EXCLUDES omits created_at/updated_at from
    every filterable registry on purpose. Widening that whitelist to serve a
    date picker would weaken a deliberate policy across all eleven resources.

THE RULE
Windows are ROLLING and INCLUSIVE OF TODAY: "last 7 days" is today plus the six
days before it. Keys state the window LENGTH rather than a calendar unit, because
"last month" reads as "the previous calendar month" and would be wrong by up to
30 days against a rolling window. book_event/views.py's reports action keeps its
own older "month"/"year" keys, which mean CALENDAR to date — a different question,
under different names, on purpose.

A ROW WITH NO DATE IS OUTSIDE EVERY WINDOW
It cannot be placed in time, so it belongs to "all" and to nothing else. Callers
that care report the count rather than letting rows vanish unexplained.
"""
from datetime import timedelta

from django.db.models.functions import Coalesce
from django.utils import timezone

# Keys are the wire contract, shared with frontend/src/lib/constants.js
# DASH_PERIODS. Anything not listed here is rejected, never silently treated as
# "all" — a filter that ignores its input is indistinguishable from a broken one.
PERIOD_DAYS = {
    "all": None,
    "last_7_days": 7,
    "last_30_days": 30,
    "last_12_months": 365,
}

PERIOD_ALL = "all"


class PeriodError(ValueError):
    """An unrecognised period key. Carries the message sent to the client."""


def period_window(period, today):
    """(from_date, to_date) for a period key — (None, None) for "all"."""
    days = PERIOD_DAYS[period]
    if days is None:
        return None, None
    return today - timedelta(days=days - 1), today


def today_for_period():
    """
    The date "today" resolves to for every window in the CRM.

    settings.TIME_ZONE is "UTC", so this is the UTC date — the same thing
    book_event/views.py gets from timezone.now().date(). Deliberately identical:
    two date filters in one CRM that disagree about when today ends is worse than
    either convention alone. The consequence for anyone operating well east of
    UTC is that the window ends on what they call yesterday for the first hours
    of their day; fixing that is a TIME_ZONE decision, not a per-view one.
    """
    return timezone.localdate()


def resolve_period(value):
    """
    (period_key, from_date, to_date) for a raw query-param value.

    Raises PeriodError for anything unknown. An empty or absent value is "all",
    because "no preset chosen" and "every record" are the same request.
    """
    period = (value or PERIOD_ALL).strip() or PERIOD_ALL
    if period not in PERIOD_DAYS:
        raise PeriodError(
            f"Unknown period {period!r}. "
            f"Expected one of: {', '.join(sorted(PERIOD_DAYS))}."
        )
    p_from, p_to = period_window(period, today_for_period())
    return period, p_from, p_to


def date_expression(fields):
    """
    The single date expression for a list of candidate columns, in priority order.

    One field is passed through as its own name so the common case adds no SQL;
    several are COALESCEd, which is what makes "dated by request_date, else
    invoice_date" one filterable value rather than two criteria a caller has to
    remember to OR together.
    """
    if not fields:
        raise ValueError("date_expression() needs at least one field")
    if len(fields) == 1:
        return fields[0]
    return Coalesce(*fields)


def apply_period(qs, fields, p_from, p_to):
    """
    `qs` narrowed to [p_from, p_to] over `fields`, or unchanged for "all".

    Uses .alias() rather than .annotate(): the window is a WHERE clause, and an
    annotation would join any subsequent .values()/GROUP BY and silently change
    what an aggregate groups by.
    """
    if p_from is None:
        return qs
    expr = date_expression(fields)
    if isinstance(expr, str):
        return qs.filter(**{f"{expr}__gte": p_from, f"{expr}__lte": p_to})
    return qs.alias(_period_date=expr).filter(
        _period_date__gte=p_from, _period_date__lte=p_to,
    )


def undated_count(qs, fields):
    """How many rows carry no date at all, and so sit outside every window."""
    expr = date_expression(fields)
    if isinstance(expr, str):
        return qs.filter(**{f"{expr}__isnull": True}).count()
    return qs.alias(_period_date=expr).filter(_period_date__isnull=True).count()


class PeriodFilterMixin:
    """
    Adds `?period=<key>` to a ModelViewSet's list.

    Declare the date columns, most authoritative first:

        class TicketViewSet(PeriodFilterMixin, ...):
            period_date_fields = ("created_at",)

    Applied in filter_queryset() rather than get_queryset() so it composes with
    filter_spec, search and ordering instead of racing them, and so a detail
    route (retrieve/update on one row) is never narrowed by a window the user
    happened to leave selected — fetching a booking by id must not 404 because it
    is older than 30 days.

    Rejects an unknown key with 400. Every response also carries an
    `X-Period-From` / `X-Period-To` header pair, so the client can show the exact
    window it is looking at without a second endpoint to ask.
    """

    #: Date columns in priority order. COALESCEd when more than one.
    period_date_fields = ()

    #: Actions the window applies to. List-shaped reads only, by default.
    period_actions = ("list",)

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        if not self.period_date_fields:
            return queryset
        if self.action not in self.period_actions:
            return queryset

        period, p_from, p_to = self.resolved_period()
        if p_from is None:
            return queryset
        return apply_period(queryset, self.period_date_fields, p_from, p_to)

    def resolved_period(self):
        """
        (key, from, to) for this request, raising DRF ValidationError on a bad key.

        Cached per request: filter_queryset and any action that wants to report
        the window both need it, and parsing twice is how the two could ever
        answer differently.
        """
        from rest_framework.exceptions import ValidationError

        if not hasattr(self, "_resolved_period"):
            raw = self.request.query_params.get("period")
            try:
                self._resolved_period = resolve_period(raw)
            except PeriodError as exc:
                raise ValidationError({"period": str(exc)})
        return self._resolved_period

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if self.period_date_fields and getattr(self, "_resolved_period", None):
            period, p_from, p_to = self._resolved_period
            response["X-Period"] = period
            response["X-Period-From"] = p_from.isoformat() if p_from else ""
            response["X-Period-To"] = p_to.isoformat() if p_to else ""
        return response
