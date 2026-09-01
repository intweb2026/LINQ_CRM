"""
mining_matrix/services.py
──────────────────────────
Every number the Mining Resource Matrix shows, computed live.

WHAT THE MATRIX IS
One row per event, answering "how much market-research output is still waiting to
be mined for this event, and how is it split by priority". Nothing is stored: the
figures are aggregates over Ticket Central, joined to the Events catalogue through
codes.canonical_code, so they cannot go stale and there is no sync to run.

UNMINED, DEFINED ONCE
`actual_number IS NULL`. That column is the Data Mining result — it is written
when DMD reports how many contacts a link actually yielded (constants.DMD_FIELDS)
— so a NULL is precisely "raised, not yet worked". Zero is NOT unmined: a link
that was mined and yielded nothing is finished work, and lumping it in here would
put it back on the queue for ever.

TWO QUERIES, WHATEVER THE ROW COUNT
The aggregate is a single GROUP BY (purpose, priority) over the unmined rows, and
the catalogue is a single values() over Events. Everything after that is
dictionary work in Python. There is deliberately no per-event query: the obvious
shape for this page is one COUNT per row, which on a 400-event catalogue is 400
round trips for a table nobody would call slow to render.
"""
from collections import defaultdict
from datetime import date

from django.db.models import Count, Sum
from django.utils import timezone

from events.models import Event
from ticket_central.models import Ticket
from ticket_central.scoping import scope_tickets

from .codes import known_purpose_codes, resolve_codes

# The three views the page offers. `upcoming` is the default: the matrix is a
# resource-planning tool, and work for an event that has already run cannot be
# planned for.
VIEW_UPCOMING = "upcoming"
VIEW_ALL = "all"
VIEW_UNLINKED = "unlinked"
VIEWS = (VIEW_UPCOMING, VIEW_ALL, VIEW_UNLINKED)

# An edition in one of these has no commencement left to count down to, whatever
# its date column says. Both are excluded from `upcoming` even when the date is
# in the future — a cancelled event dated next March is not upcoming work.
CLOSED_STATUSES = frozenset({Event.Status.COMPLETED, Event.Status.CANCELLED})

# Ticket.priority is a free CharField (the D4 note in ticket_central/models.py:
# Zoho's values vary), so the enum is the PREFERRED ORDER of the columns, not the
# permitted set. Anything else observed in the data is appended rather than
# dropped — a priority the matrix refused to show would be unmined work invisible
# on the one screen that exists to surface it.
PRIORITY_ORDER = tuple(Ticket.Priority.values)

# The bucket for a ticket whose priority was never filled in. Keyed on "" so it
# round-trips as a filter value; labelled for the column header.
BLANK_PRIORITY = ""
BLANK_PRIORITY_LABEL = "No priority"


def _today():
    """Local calendar date, so "days to go" matches the viewer's own Monday."""
    return timezone.localdate()


# ── The aggregate ────────────────────────────────────────────────────────────

def unmined_by_purpose(user):
    """
    {PURPOSE_UPPER: bucket} over every unmined ticket this user may see.

    bucket = {
        "links":      int,                       # rows
        "estimate":   int,                       # SUM(estimate)
        "priorities": {PRI: {"links", "estimate"}},
    }

    SCOPED, deliberately. The row a user clicks navigates to Ticket Central
    filtered on the same purpose, and that table applies author scoping — so an
    unscoped aggregate here would quote a total the destination cannot show. See
    ticket_central/scoping.py.

    Estimate is summed NULL-tolerantly: `estimate` is nullable, and a ticket
    raised without one still counts as an unmined LINK even though it contributes
    nothing to the unmined DATA. Reporting it in Col C but not Col D is the honest
    answer; skipping the row entirely would hide it.

    A ticket with a BLANK purpose is bucketed under None. It belongs to no event
    and cannot be given one here, so it is reported as its own figure in
    build_payload rather than silently discarded.
    """
    rows = (
        scope_tickets(Ticket.objects.filter(actual_number__isnull=True), user)
        .values("purpose", "priority")
        .annotate(links=Count("id"), estimate=Sum("estimate"))
    )

    out = {}
    for row in rows:
        code = (row["purpose"] or "").strip().upper() or None
        priority = (row["priority"] or "").strip().upper()
        links = row["links"] or 0
        estimate = row["estimate"] or 0

        bucket = out.get(code)
        if bucket is None:
            bucket = {
                "links": 0,
                "estimate": 0,
                "priorities": defaultdict(lambda: {"links": 0, "estimate": 0}),
            }
            out[code] = bucket
        bucket["links"] += links
        bucket["estimate"] += estimate
        bucket["priorities"][priority]["links"] += links
        bucket["priorities"][priority]["estimate"] += estimate
    return out


def priority_columns(buckets):
    """
    The priority columns to draw, in a stable order, for THESE rows only.

    Enum order first so the familiar columns never move, then any unexpected
    value alphabetically, then the blank bucket last. A priority carrying no
    unmined work anywhere in the result is omitted: the table is already wide, and
    a column of zeros on every row is width spent saying nothing.
    """
    seen = set()
    for bucket in buckets:
        seen.update(bucket["priorities"].keys())

    ordered = [p for p in PRIORITY_ORDER if p in seen]
    extra = sorted(p for p in seen if p and p not in PRIORITY_ORDER)
    cols = ordered + extra
    if BLANK_PRIORITY in seen:
        cols.append(BLANK_PRIORITY)
    return [
        {"key": p, "label": BLANK_PRIORITY_LABEL if p == BLANK_PRIORITY else p}
        for p in cols
    ]


# ── Row assembly ─────────────────────────────────────────────────────────────

def _empty_bucket():
    return {"links": 0, "estimate": 0, "priorities": {}}


def _row(*, event_code, canonical, event, bucket, today):
    """One matrix row. `event` is None for a code the catalogue does not carry."""
    start = getattr(event, "event_date", None)
    end = getattr(event, "end_date", None)
    days = (start - today).days if start else None

    priorities = bucket["priorities"]
    return {
        # Col A — the code AS THE EVENTS MODULE HOLDS IT, which is what the user
        # recognises. `canonical_code` beside it is what the figures were actually
        # gathered under, and what the click-through filters on; showing both is
        # the only way a row that resolved oddly can be spotted.
        "event_code": event_code,
        "canonical_code": canonical,
        "event_name": getattr(event, "name", "") or "",
        "status": getattr(event, "status", "") or "",
        "location": (getattr(event, "location", "") or getattr(event, "city", "") or ""),
        # Col B
        "start_date": start,
        "end_date": end,
        "days_to_go": days,
        # Col C / Col D
        "unmined_links": bucket["links"],
        "unmined_data": bucket["estimate"],
        # Col E onwards. Estimate is the headline figure, so these sum to Col D;
        # the counts ride along for the hover, and sum to Col C.
        "priority_data": {k: v["estimate"] for k, v in priorities.items()},
        "priority_links": {k: v["links"] for k, v in priorities.items()},
        # True when this event code resolved onto a purpose Ticket Central has
        # actually raised work under. False means the join found nothing — the
        # zeros read "no tickets exist", not "everything is mined".
        "matched": bool(canonical) and bucket["links"] > 0,
        "linked": event is not None,
    }


def _context(user, today):
    """
    The work every view shares: the aggregate, the catalogue, the code join, and
    which editions are still ahead.

    Pulled out so one request answers for all three views. The alternative — a
    `summary` endpoint calling build_payload three times — costs three copies of
    the same two queries to produce three numbers that are already derivable here.
    """
    known = known_purpose_codes()
    buckets = unmined_by_purpose(user)

    events = list(
        Event.objects.only(
            "event_code", "name", "status", "event_date", "end_date",
            "location", "city",
        ).order_by("event_date", "event_code")
    )
    resolved = resolve_codes([e.event_code for e in events], known)

    # Which canonical codes the DEFAULT view accounts for. Computed whichever
    # view was asked for, because `unlinked` is defined as the complement of this
    # set and the two must not be able to disagree.
    upcoming_events = [
        e for e in events
        if e.event_date and e.event_date >= today and e.status not in CLOSED_STATUSES
    ]
    upcoming_codes = {resolved[e.event_code] for e in upcoming_events}
    return buckets, events, resolved, upcoming_events, upcoming_codes


def _view_counts(buckets, events, resolved, upcoming_events, upcoming_codes,
                 include_zero):
    """
    Rows each view would render, for the tab strip.

    Counted by the SAME predicates build_payload filters on, one line apart, so a
    tab's number and the table beneath it cannot disagree — the failure
    TicketViewSet.stats documents at length. `include_zero` is honoured here too,
    or turning it on would grow the table without the tab admitting it.
    """
    def rows_for(source):
        if include_zero:
            return len(source)
        return sum(
            1 for e in source
            if (buckets.get(resolved[e.event_code]) or _empty_bucket())["links"] > 0
        )

    return {
        VIEW_UPCOMING: rows_for(upcoming_events),
        VIEW_ALL: rows_for(events),
        VIEW_UNLINKED: len([c for c in buckets if c and c not in upcoming_codes]),
    }


def build_payload(user, view=VIEW_UPCOMING, include_zero=False):
    """
    The full response for one view.

      upcoming  events whose start date is today or later and whose status is
                neither Completed nor Cancelled, soonest first. The default,
                because it is the only view that answers "what has to be mined
                next". AFS, AFS - JS and Feb2027_AFS-JS cannot all be upcoming at
                once, so the identical-triplet problem does not arise here.
      all       every event in the catalogue, soonest first, past editions
                included. Repeats ARE possible here and the duplicated rows carry
                identical figures, which is why `totals` is computed over DISTINCT
                canonical codes rather than by adding the column up.
      unlinked  the complement: every purpose holding unmined work that `upcoming`
                does NOT account for, because the catalogue has no event for that
                code, or only events that are past, Completed or Cancelled.
                Without this view that work would be invisible on every screen the
                matrix offers.
    """
    if view not in VIEWS:
        view = VIEW_UPCOMING

    today = _today()
    buckets, events, resolved, upcoming_events, upcoming_codes = _context(user, today)

    if view == VIEW_UNLINKED:
        rows = _unlinked_rows(buckets, events, resolved, upcoming_codes, today)
    else:
        source = upcoming_events if view == VIEW_UPCOMING else events
        rows = []
        for event in source:
            code = resolved[event.event_code]
            bucket = buckets.get(code) or _empty_bucket()
            if not include_zero and bucket["links"] == 0:
                continue
            rows.append(_row(
                event_code=event.event_code, canonical=code,
                event=event, bucket=bucket, today=today,
            ))

    columns = priority_columns(
        [buckets[c] for c in {r["canonical_code"] for r in rows} if c in buckets]
    )
    no_purpose = buckets.get(None) or _empty_bucket()

    return {
        "view": view,
        "today": today,
        "include_zero": include_zero,
        "priority_columns": columns,
        "rows": rows,
        "totals": _totals(rows, buckets, columns),
        "view_counts": _view_counts(
            buckets, events, resolved, upcoming_events, upcoming_codes, include_zero,
        ),
        # Unmined tickets carrying no purpose at all. They can never be a row —
        # there is nothing to group them under — so they are reported as a figure
        # rather than dropped without trace.
        "no_purpose": {"links": no_purpose["links"], "estimate": no_purpose["estimate"]},
    }


def _unlinked_rows(buckets, events, resolved, upcoming_codes, today):
    """
    One row per purpose holding unmined work that the default view misses.

    Where the catalogue does carry the code, the MOST RECENT edition is attached
    for context, so the row can say why it is here — ran last March, or Cancelled
    — rather than only that it is. `events` is date-ascending, so the last match
    wins.
    """
    latest_for = {}
    for event in events:
        latest_for[resolved[event.event_code]] = event

    rows = []
    for code in sorted(c for c in buckets if c):
        if code in upcoming_codes:
            continue
        event = latest_for.get(code)
        rows.append(_row(
            event_code=(event.event_code if event else code),
            canonical=code, event=event, bucket=buckets[code], today=today,
        ))
    # Newest first, and a code with no event at all sorts last: among the rest,
    # the recently-run editions are the ones still worth chasing.
    rows.sort(
        key=lambda r: (r["start_date"] is not None, r["start_date"] or date.min),
        reverse=True,
    )
    return rows


def _totals(rows, buckets, columns):
    """
    The footer, counted over DISTINCT canonical codes.

    Adding the visible column up would be wrong in the `all` view and only there,
    which is the worst kind of wrong: three editions of AFS each show the same 412
    unmined links, because Ticket Central has one AFS purpose and not three. So
    the total is taken from the underlying buckets, once per code, and it stays
    right in every view without the caller having to know which one it is in.
    """
    codes = {r["canonical_code"] for r in rows if r["canonical_code"] in buckets}
    by_priority = {}
    for col in columns:
        key = col["key"]
        by_priority[key] = sum(
            buckets[c]["priorities"].get(key, {}).get("estimate", 0) for c in codes
        )
    return {
        "codes": len(codes),
        "rows": len(rows),
        "unmined_links": sum(buckets[c]["links"] for c in codes),
        "unmined_data": sum(buckets[c]["estimate"] for c in codes),
        "priority_data": by_priority,
    }
