"""
performance_matrix/services.py
───────────────────────────────
Every number the Performance Matrix shows, computed live.

ONE ROW PER EDITION. An edition is one Event row, identified by
(base_code, year); the internal event_code is its label. See events/codes.py.

THE JOIN, AND WHY IT IS NOT event_code = event_code WITH edition = year.
Bookings store an event as a text code plus an optional edition year. In the
live data 14,954 of 15,097 delegates carry NO edition, and no invoice records
the event date it was sold for, so a strict (code, year) match returns zero for
nearly every event. Instead:

  1. every booking code is resolved to a FAMILY through the catalogue, matching
     either an edition's internal code or the family's base code;
  2. inside the family the booking lands on an edition by its explicit edition
     year when it has one, otherwise by its request date (booked_on) falling in
     that edition's SALES WINDOW: after the previous edition ended, up to this
     edition's last day. The first known edition of a family looks back
     FIRST_EDITION_LOOKBACK_DAYS, because there is no earlier edition to bound it.

Paper reviews land on an edition the same way, by submission date. Unmined
tickets are not dated work for a past edition, so a family's unmined pile is
shown once, on its nearest upcoming edition.

ponytail: the lookback is a heuristic ceiling. Backfilling `edition` on the
invoices (or recording the event date they were sold for) makes rule 2 exact and
retires the window; nothing else here changes.

THE PREVIOUS-EDITION LABEL READS THE MATRIX ITSELF. Fresh, Repeat, Rescheduled
and Relaunch are decided by the VERDICT the admin gave the prior edition in this
module and by nothing else; a prior edition with no verdict counts as one that
ran. That is what makes it scale: setting this year's verdicts labels next year.

ONE QUERY PER SOURCE, WHATEVER THE ROW COUNT. Delegates, paper reviews and the
ticket aggregate are each read once and every figure is dictionary work in
Python. ~15k delegate rows is well under 200 ms; the per-event query the obvious
shape would issue is two hundred round trips for a table nobody would call slow.
"""
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.db.models import CharField, DateField, F, Value
from django.db.models.functions import Coalesce, NullIf, Upper

from book_delegate.models import BookDelegate
from events.codes import derive_base_code
from events.models import Event
from events.serializers import team_owner_defaults
from mining_matrix.services import SPLIT_TYPE, split_columns, unmined_by_purpose
from paper_review.models import PaperReview

VIEW_UPCOMING = "upcoming"
VIEW_ALL = "all"
VIEWS = (VIEW_UPCOMING, VIEW_ALL)

# A delegate "counts" while money is in or expected. Free, cancelled, refunded
# and credit rows are out. Spelled as the STORED values.
LIVE_STATUSES = frozenset({"Paid", "Paid (Transferred)", "Pending"})
PAID = "Paid"
PENDING = "Pending"
# The stored value the UI labels "Payable" (frontend lib/constants.js).
PAYABLE = "Paid"
DEAD_STATUSES = frozenset({"Cancelled", "Refunded"})

BENCHMARK = 40                    # paid heads every edition is measured against
PENDING_GRACE_DAYS = 14           # an invoice older than this is Pending, younger is Expected
FIRST_EDITION_LOOKBACK_DAYS = 365

# Prior edition's verdict -> this edition's label.
RESCHEDULED_FROM = frozenset({"Postponed", "TBP"})
RELAUNCH_FROM = frozenset({"Cancelled"})

# (key, min age in days, max age in days) — how old a date is from today.
WINDOWS = (("today", 0, 0), ("d7", 0, 7), ("d14", 8, 14), ("d21", 15, 21), ("d30", 0, 30))

# The owner columns the hover card shows, in reading order. A blank column
# inherits the owning team's lead exactly as the Events table does
# (events.serializers.team_owner_defaults); the values that mean "nobody" mirror
# OwnerResolutionMixin._BLANK_OWNER_VALUES.
OWNER_FIELDS = (
    ("sales_team", "SCA"), ("team_leader", "Sales lead"),
    ("telemarketing_team", "Telemarketing"), ("market_research_senior", "MR senior"),
    ("market_research_junior", "MR junior"), ("spex_team", "SpEx"),
)
BLANK_OWNER = frozenset({"", "-", "–", "—"})


def _blank():
    z = {k: 0 for k in ("live", "paid", "pending", "expected", "pr_total")}
    for key, _, _ in WINDOWS:
        z["bk_" + key] = 0
        z["pay_" + key] = 0
        z["pr_" + key] = 0
    return z


def _delegate_rows():
    """
    (code, edition, status, payable/free, payment date, invoice date, booked_on)
    per delegate, with every per-delegate override already resolved against its
    invoice. The overrides are NULL-or-blank when not set, hence NullIf.
    """
    blank = Value("")
    return (
        BookDelegate.objects.filter(delegate_count=1)
        .annotate(
            code=Upper(F("invoice__event_code")),
            eff_status=Coalesce(NullIf(F("delegate_payment_status"), blank),
                                F("invoice__payment_status"), output_field=CharField()),
            eff_pof=Coalesce(NullIf(F("delegate_paid_or_free"), blank),
                             F("invoice__paid_or_free"), output_field=CharField()),
            eff_pay=Coalesce(F("delegate_payment_date"), F("invoice__payment_date"),
                             output_field=DateField()),
            eff_inv=Coalesce(F("delegate_invoice_date"), F("invoice__invoice_date"),
                             output_field=DateField()),
        )
        .values_list("code", "invoice__edition", "eff_status", "eff_pof",
                     "eff_pay", "eff_inv", "booked_on")
        .iterator(chunk_size=2000)
    )


def countdown(today, d):
    """'2mo 12d', '3d', 'Today', '5d ago' — the Countdown column's label."""
    if d == today:
        return "Today"
    a, b = (today, d) if d > today else (d, today)
    rd = relativedelta(b, a)
    parts = [f"{rd.years}y" if rd.years else "", f"{rd.months}mo" if rd.months else "",
             f"{rd.days}d" if rd.days else ""]
    label = " ".join(p for p in parts if p)
    return label if d > today else label + " ago"


def _families(events):
    """{BASE: [editions sorted by start]} plus {any code: BASE}."""
    fam = defaultdict(list)
    for e in events:
        fam[(e.base_code or derive_base_code(e.event_code)).upper()].append(e)
    code_to_base = {}
    for base, eds in fam.items():
        eds.sort(key=lambda e: (e.event_date, e.pk))
        code_to_base[base] = base
        for e in eds:
            code_to_base[e.event_code.upper()] = base
    return fam, code_to_base


def _windows(fam):
    """{event pk: (first day, last day)} a booking may land in for that edition."""
    win = {}
    for eds in fam.values():
        prev_end = None
        for e in eds:
            end = e.end_date or e.event_date
            start = prev_end + timedelta(days=1) if prev_end else e.event_date - timedelta(days=FIRST_EDITION_LOOKBACK_DAYS)
            win[e.pk] = (start, end)
            prev_end = end
    return win


def _place(row_edition, on, editions, win):
    """The edition a dated row belongs to, or None. Explicit year first, window second."""
    if row_edition and 2000 <= row_edition <= 2100:
        return next((e for e in editions if e.year == row_edition), None)
    if on is None:
        return None
    for e in editions:
        lo, hi = win[e.pk]
        if lo <= on <= hi:
            return e
    return None


def _age_buckets(stats, prefix, today, d):
    age = (today - d).days
    for key, lo, hi in WINDOWS:
        if lo <= age <= hi:
            stats[prefix + key] += 1


def previous_edition_label(prior):
    """
    Fresh      no earlier edition of this family in the catalogue
    Repeat     the prior edition ran
    Rescheduled the prior edition was Postponed or TBP, so this one is its new date
    Relaunch   the prior edition was Cancelled and the family is back

    The prior edition's VERDICT decides, because that is what the admin actually
    recorded in this module. No verdict reads as Repeat: the edition existed and
    nobody marked it as anything else.
    """
    if prior is None:
        return "Fresh"
    outcome = prior.verdict
    if outcome in RESCHEDULED_FROM:
        return "Rescheduled"
    if outcome in RELAUNCH_FROM:
        return "Relaunch"
    return "Repeat"


def _owners(e, defaults):
    out = {}
    for field, label in OWNER_FIELDS:
        v = (getattr(e, field, "") or "").strip()
        if v in BLANK_OWNER:
            d = defaults.get(field)
            v = ", ".join(d["names"]) if d else ""
        if v:
            out[label] = v
    return out


def _ticket_targets(fam, today):
    """{event pk: BASE} for the one edition per family that carries its unmined pile."""
    out = {}
    for base, eds in fam.items():
        target = next((e for e in eds if (e.end_date or e.event_date) >= today), eds[-1])
        out[target.pk] = base
    return out


def build_payload(view=VIEW_UPCOMING, today=None, user=None):
    today = today or date.today()
    events = list(Event.objects.order_by("event_date", "pk"))
    fam, code_to_base = _families(events)
    win = _windows(fam)
    owner_defaults = team_owner_defaults()

    stats = defaultdict(_blank)
    live_dates = defaultdict(list)      # event pk -> booked_on of every live delegate
    grace = today - timedelta(days=PENDING_GRACE_DAYS)

    for code, edition, status, pof, pay_date, inv_date, booked_on in _delegate_rows():
        base = code_to_base.get(code or "")
        if base is None:
            continue
        ed = _place(edition, booked_on, fam[base], win)
        if ed is None:
            continue
        s = stats[ed.pk]
        live = status in LIVE_STATUSES
        payable = pof == PAYABLE
        if live:
            s["live"] += 1
            if booked_on:
                live_dates[ed.pk].append(booked_on)
                _age_buckets(s, "bk_", today, booked_on)
        if pay_date and payable and status not in DEAD_STATUSES:
            s["paid"] += 1
        if status == PENDING and payable:
            if inv_date and inv_date < grace:
                s["pending"] += 1
            else:
                s["expected"] += 1
        if status == PAID and payable and pay_date:
            _age_buckets(s, "pay_", today, pay_date)

    for dates in live_dates.values():
        dates.sort()

    # Paper reviews, by submission date, placed like a booking with no edition.
    for code, submitted in PaperReview.objects.values_list("event_code", "paper_submission_date").iterator(chunk_size=2000):
        base = code_to_base.get((code or "").upper())
        if base is None:
            continue
        ed = _place(None, submitted, fam[base], win)
        if ed is None:
            continue
        stats[ed.pk]["pr_total"] += 1
        _age_buckets(stats[ed.pk], "pr_", today, submitted)

    # Unmined tickets, once per family on its nearest upcoming edition. Scoped
    # exactly as Ticket Central scopes them for this caller; the viewset is admin
    # only, so in practice that is every ticket.
    buckets = unmined_by_purpose(user) if user is not None else {}
    ticket_types = split_columns(list(buckets.values())).get(SPLIT_TYPE, []) if buckets else []
    ticket_target = _ticket_targets(fam, today)

    rows = []
    for e in events:
        end = e.end_date or e.event_date
        if view == VIEW_UPCOMING and end < today:
            continue
        base = code_to_base[e.event_code.upper()]
        prior = next((p for p in reversed(fam[base]) if p.event_date < e.event_date), None)
        days_left = (e.event_date - today).days
        s = stats.get(e.pk) or _blank()

        live_prev = None
        if prior is not None:
            cutoff = prior.event_date - timedelta(days=days_left)
            live_prev = bisect_right(live_dates.get(prior.pk, []), cutoff)

        bucket = buckets.get(base) if ticket_target.get(e.pk) == base else None
        type_links = {k: v["links"] for k, v in bucket["splits"][SPLIT_TYPE].items()} if bucket else {}

        rows.append({
            "id": e.pk,
            "event_code": e.event_code,
            "base_code": base,
            "year": e.year,
            "name": e.official_event_name or e.name or e.event_code,
            "location": e.location or e.city or "",
            "owners": _owners(e, owner_defaults),
            "start_date": e.event_date.isoformat(),
            "end_date": end.isoformat(),
            "days_left": days_left,
            "done": end < today,
            "countdown": countdown(today, e.event_date),
            "prev_status": previous_edition_label(prior),
            "prior_event_code": prior.event_code if prior else None,
            "live_count": s["live"],
            "paid_heads": s["paid"],
            "pending": s["pending"],
            "expected": s["expected"],
            "shortfall": max(0, BENCHMARK - s["paid"]),
            "live_prev_year": live_prev,
            "live_delta": (s["live"] - live_prev) if live_prev is not None else None,
            **{"bk_" + k: s["bk_" + k] for k, _, _ in WINDOWS},
            **{"pay_" + k: s["pay_" + k] for k, _, _ in WINDOWS},
            "pr_total": s["pr_total"],
            **{"pr_" + k: s["pr_" + k] for k, _, _ in WINDOWS},
            "tk_unmined": bucket["links"] if bucket else 0,
            "tk_data": bucket["estimate"] if bucket else 0,
            "tk_types": type_links,
            "tk_here": bucket is not None,
            "verdict": e.verdict or "",
        })

    rows.sort(key=lambda r: (r["days_left"] < 0, abs(r["days_left"]), r["start_date"]))

    totals = {
        "events": len(rows),
        "live": sum(r["live_count"] for r in rows),
        "paid": sum(r["paid_heads"] for r in rows),
        "pending": sum(r["pending"] for r in rows),
        "expected": sum(r["expected"] for r in rows),
        "below_benchmark": sum(1 for r in rows if r["shortfall"] > 0),
        "bk_d7": sum(r["bk_d7"] for r in rows),
        "pay_d7": sum(r["pay_d7"] for r in rows),
        "pr_d7": sum(r["pr_d7"] for r in rows),
        "tk_unmined": sum(r["tk_unmined"] for r in rows),
    }
    return {
        "today": today.isoformat(),
        "view": view,
        "benchmark": BENCHMARK,
        "verdicts": list(Event.Verdict.values),
        "years": sorted({e.year for e in events if e.year}),
        "ticket_types": ticket_types,
        "rows": rows,
        "totals": totals,
    }
