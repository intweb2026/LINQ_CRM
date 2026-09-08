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
PENDING_GRACE_DAYS = 14           # a pending invoice older than this is Pending, 14 days or newer is Expected, none yet is Not invoiced
FIRST_EDITION_LOOKBACK_DAYS = 365

# Prior edition's verdict -> this edition's label.
RESCHEDULED_FROM = frozenset({"Postponed", "TBP"})
RELAUNCH_FROM = frozenset({"Cancelled"})

# (key, min age in days, max age in days) — how old a date is from today.
WINDOWS = (("today", 0, 0), ("d7", 0, 7), ("d14", 8, 14), ("d21", 15, 21), ("d30", 0, 30))

# ── Projection ────────────────────────────────────────────────────────────────
# Sales open six months before the event and heads land on a front-loaded
# curve: a third by three months in, a third more by five, the rest in the last
# month. Today's live count over the share elapsed is the finish it points to.
SALES_MONTHS = 6
CURVE = ((3, 0.33), (5, 0.33), (None, 0.34))   # (months after opening, share landed by then); None = event day

# The weekly curves the team runs in Sheets: the share of the FINAL attendance
# and paid heads an edition should have banked with this many whole weeks to go,
# read top-down as "more than N weeks out". Nothing is expected beyond 21 weeks
# and the -1 row is the event week. The health columns divide today's figure by
# this share; the colour bands over the result live with the page.
ATT_CURVE = ((21, 0), (18, .225), (16, .346), (13, .413), (11, .549), (9, .607),
             (7, .7), (5, .729), (3, .855), (1, .943), (-1, 1))
PAY_CURVE = ((21, 0), (18, .2), (16, .343), (13, .423), (11, .571), (9, .667),
             (7, .769), (5, .833), (3, .9), (1, .976), (-1, 1))

# The owner columns the hover card shows, in reading order. A blank column
# inherits the owning team's lead exactly as the Events table does
# (events.serializers.team_owner_defaults); the values that mean "nobody" mirror
# OwnerResolutionMixin._BLANK_OWNER_VALUES.
OWNER_FIELDS = (
    ("sales_team", "SCA"),
    ("telemarketing_team", "Telemarketing"), ("market_research_senior", "MR senior"),
    ("market_research_junior", "MR junior"), ("spex_team", "SpEx"),
)
BLANK_OWNER = frozenset({"", "-", "–", "—"})

# Booking codes are free text in a house vocabulary ("Speaker", "Speaker / SLV
# SpEx", "Upgraded to GLD SpEx", "Group Pass", "Speaker Table"). Every test on
# them is a casefolded substring, so a code naming two things counts for both;
# "Speaker Table" is a sponsor, not a speaker, and is carved out by name.
PAID_STATUSES = frozenset({"Paid", "Paid (Transferred)"})
FREE = "Free"
CANCELLED = "Cancelled"
SPEX_TIERS = (("ptn", "ptn"), ("plt", "plt"), ("gld", "gld"), ("slv", "slv"),
              ("table", "speaker table"), ("upgraded", "upgraded"))
SPEX_STATES = ("all", "paid", "pending")
SPEX_KEYS = tuple(f"spex_{st}_{t}" for st in SPEX_STATES for t in ("total",) + tuple(k for k, _ in SPEX_TIERS))


def _is_speaker(lc):
    return ("speaker" in lc or "spp" in lc) and "speaker table" not in lc


def _is_spex(lc):
    return "spex" in lc or "speaker table" in lc


def _blank():
    z = {k: 0 for k in ("live", "paid", "pending", "expected", "not_invoiced", "free", "cancelled",
                        "sp_total", "sp_booked", "sp_paid", "sp_free", "pr_total")}
    z.update(bk_last=None, pay_last=None, sp_first=None)
    for key, _, _ in WINDOWS:
        z["bk_" + key] = 0
        z["pay_" + key] = 0
        z["pr_" + key] = 0
    return z


def _delegate_rows():
    """
    (code, edition, status, payable/free, payment date, invoice date, booked_on,
    booking code, company) per delegate, with every per-delegate override already
    resolved against its invoice. The overrides are NULL-or-blank when not set,
    hence NullIf.
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
                     "eff_pay", "eff_inv", "booked_on", "invoice__booking_code", "invoice__company_name")
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


def _round(x):
    return int(x + 0.5)     # half up, as the Sheets formulas round


def curve_share(today, event_date):
    """Share of the sales curve elapsed: 0 before sales open, 1 from the event day."""
    start = event_date - relativedelta(months=SALES_MONTHS)
    if today < start:
        return 0.0
    if today >= event_date:
        return 1.0
    lo, done = start, 0.0
    for months, share in CURVE:
        hi = start + relativedelta(months=months) if months else event_date
        if today <= hi:
            return done + share * (today - lo).days / (hi - lo).days
        lo, done = hi, done + share
    return 1.0


def projection(today, event_date, live):
    """The finish today's live count points to; None before sales open (the UI reads Pending)."""
    share = curve_share(today, event_date)
    return _round(live / share) if share else None


def curve_projection(today, event_date, actual, curve):
    """
    The finish `actual` points to on a weekly curve: the actual over the share
    the curve expects banked with this many whole weeks to go, so it IS the
    actual from the event week on. None while the curve expects nothing yet.
    """
    weeks = max(0, (event_date - today).days // 7)
    share = next(s for lo, s in curve if weeks > lo)
    return _round(actual / share) if share else None


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
    companies = defaultdict(lambda: defaultdict(set))   # event pk -> {"gp" | spex key: {company}}
    grace = today - timedelta(days=PENDING_GRACE_DAYS)

    for code, edition, status, pof, pay_date, inv_date, booked_on, bcode, company in _delegate_rows():
        base = code_to_base.get(code or "")
        if base is None:
            continue
        ed = _place(edition, booked_on, fam[base], win)
        if ed is None:
            continue
        s = stats[ed.pk]
        live = status in LIVE_STATUSES
        payable = pof == PAYABLE
        paid_status = status in PAID_STATUSES
        lc = (bcode or "").casefold()
        co = (company or "").strip().casefold()
        if booked_on:
            s["bk_last"] = max(s["bk_last"] or booked_on, booked_on)    # any booking, cancelled included
        if live:
            s["live"] += 1
            if booked_on:
                live_dates[ed.pk].append(booked_on)
                _age_buckets(s, "bk_", today, booked_on)
        if pay_date and payable and status not in DEAD_STATUSES:
            s["paid"] += 1
        if paid_status and pof == FREE:
            s["free"] += 1
        if status == CANCELLED and payable and pay_date:
            s["cancelled"] += 1
        if status == PENDING and payable:
            if inv_date is None:
                s["not_invoiced"] += 1
            elif inv_date < grace:
                s["pending"] += 1
            else:
                s["expected"] += 1
        if status == PAID and payable and pay_date:
            _age_buckets(s, "pay_", today, pay_date)
            s["pay_last"] = max(s["pay_last"] or pay_date, pay_date)
        if not live:
            continue
        # Speakers, sponsors and group passes, read off the booking code. A
        # sponsor is a COMPANY, counted once per column however many seats it
        # holds; the code's tier words each add it to that tier's set.
        if co and "group pass" in lc:
            companies[ed.pk]["gp"].add(co)
        if _is_speaker(lc):
            s["sp_total"] += 1
            if booked_on:
                s["sp_first"] = min(s["sp_first"] or booked_on, booked_on)
            if status == PENDING and not pay_date:
                s["sp_booked"] += 1
            if paid_status and payable:
                s["sp_paid"] += 1
            if paid_status and pof == FREE:
                s["sp_free"] += 1
        if co and _is_spex(lc):
            states = ["all"]
            if paid_status and payable:
                states.append("paid")
            elif status == PENDING:
                states.append("pending")
            for st in states:
                companies[ed.pk][f"spex_{st}_total"].add(co)
                for tier, needle in SPEX_TIERS:
                    if needle in lc:
                        companies[ed.pk][f"spex_{st}_{tier}"].add(co)

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
        cos = companies.get(e.pk) or {}
        paid_proj = curve_projection(today, e.event_date, s["paid"], PAY_CURVE)
        iso = lambda d: d.isoformat() if d else None  # noqa: E731

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
            "not_invoiced": s["not_invoiced"],
            "free": s["free"],
            "cancelled": s["cancelled"],
            "group_pass": len(cos.get("gp", ())),
            # Short of the benchmark on the PAYMENTS PROJECTION, not today's paid
            # heads; None while the curve expects nothing yet.
            "shortfall": max(0, BENCHMARK - paid_proj) if paid_proj is not None else None,
            "live_prev_year": live_prev,
            "live_delta": (s["live"] - live_prev) if live_prev is not None else None,
            "proj": projection(today, e.event_date, s["live"]),
            "att_proj": curve_projection(today, e.event_date, s["live"], ATT_CURVE),
            "paid_proj": paid_proj,
            **{"bk_" + k: s["bk_" + k] for k, _, _ in WINDOWS},
            "bk_last": iso(s["bk_last"]),
            **{"pay_" + k: s["pay_" + k] for k, _, _ in WINDOWS},
            "pay_last": iso(s["pay_last"]),
            "sp_first": iso(s["sp_first"]),
            **{k: s[k] for k in ("sp_total", "sp_booked", "sp_paid", "sp_free")},
            **{k: len(cos.get(k, ())) for k in SPEX_KEYS},
            "pr_total": s["pr_total"],
            **{"pr_" + k: s["pr_" + k] for k, _, _ in WINDOWS},
            "tk_unmined": bucket["links"] if bucket else 0,
            "tk_data": bucket["estimate"] if bucket else 0,
            "tk_types": type_links,
            "tk_here": bucket is not None,
            "verdict": e.verdict or "",
            "website": e.website or "",
        })

    rows.sort(key=lambda r: (r["days_left"] < 0, abs(r["days_left"]), r["start_date"]))

    totals = {
        "events": len(rows),
        "live": sum(r["live_count"] for r in rows),
        "paid": sum(r["paid_heads"] for r in rows),
        "pending": sum(r["pending"] for r in rows),
        "expected": sum(r["expected"] for r in rows),
        "not_invoiced": sum(r["not_invoiced"] for r in rows),
        "below_benchmark": sum(1 for r in rows if (r["shortfall"] or 0) > 0),
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
