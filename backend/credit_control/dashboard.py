"""
credit_control/dashboard.py
────────────────────────────
One aggregate response for the whole Dashboard tab. No pagination: every figure
here is over the entire active set, and a paginated total is not a total.

EVERY NUMBER IS DERIVED AT READ TIME. Nothing on the lead stores a count, a
bucket total or an age band, so a figure cannot be stale in a way the rest of
the page disagrees with. The cost of that is one pass over the active leads per
request, which at a few thousand rows is cheaper than the round trip.

THE SECTIONS RECONCILE, and that is a requirement rather than a nicety. Status
per caller, Status bifurcation and the aged debtor table each place every active
lead in exactly one row, so all three sum to Total Active. A manager who adds up
a column and gets a different answer stops trusting the page.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

from django.db.models import Count
from django.utils import timezone

from . import constants, engine
from .models import CreditControlLead


def _age_hours(invoice_date, now) -> float | None:
    """
    Hours since the invoice, measured from 00:00 UTC on the invoice day.

    An invoice date carries no time of day, so the clock has to start
    somewhere; starting at midnight is the only choice that does not invent
    precision the source does not have.
    """
    if not invoice_date:
        return None
    start = datetime(
        invoice_date.year, invoice_date.month, invoice_date.day,
        tzinfo=dt_timezone.utc,
    )
    return (now - start).total_seconds() / 3600.0


def _bucket_for(days: int | None) -> str | None:
    """
    Which aged-debtor band a lead falls in. Bands are contiguous and exhaustive.

    NEGATIVE DAYS ARE CLAMPED TO ZERO. An invoice can be dated tomorrow, and one
    in the live set is; it is nought days old, not un-bucketable. Left unclamped
    it matched no band, dropped out of the table, and the aged rows summed to one
    less than Total Active, which is exactly the kind of quiet disagreement that
    makes a manager stop trusting the page.
    """
    if days is None:
        return None
    days = max(days, 0)
    for low, high in constants.AGE_BUCKETS:
        if high is None:
            if days >= low:
                return f"{low}+"
        elif low <= days <= high:
            return f"{low}-{high}"
    return None


def _bucket_labels() -> list[str]:
    labels = []
    for low, high in constants.AGE_BUCKETS:
        labels.append(f"{low}+" if high is None else f"{low}-{high}")
    return labels


def _row_for(lead, code: str) -> str:
    """
    Which aged-debtor row a lead belongs to.

    Precedence matters: add-ons is tested before speaker, so a booking that
    says add-on is never counted as a speaker here. Everything that is neither
    is a delegate.
    """
    if engine.is_addons(code):
        return "Add-Ons Only"
    if engine.is_speaker(code):
        effective = lead.effective_invoice_type
        if effective == constants.INVOICE_TYPE_REQUESTED:
            return "Speakers, Requested"
        if effective == constants.INVOICE_TYPE_BLIND:
            return "Speakers, Blind Invoice"
        # Kept visible on purpose. An unclassified speaker folded into
        # Delegates would be invisible, and invisible is worse than unknown.
        return "Speakers, Unclassified"
    return "Delegates"


# THE THREE KINDS OF DEBT, and the split is the point of the table.
#
# Delegates deliberately carry NO Blind or Requested sub-rows. A delegate booked
# something, so "did they ask for this invoice" is not a question that applies;
# only a speaker can have been sent one nobody asked for. Giving Delegates the
# same sub-rows would invent a distinction the data cannot support.
#
# Sponsors are here even though they are not in the ACTIVE bucket. They are
# real money owed and they were simply missing from this table, which made the
# ageing read as the whole debt when it was only the part being phoned.
AGED_ROWS = (
    "Delegates",
    "Speakers, Requested",
    "Speakers, Blind Invoice",
    "Speakers, Unclassified",
    "Sponsors",
    "Add-Ons Only",
)


# The jobs worth telling somebody about, and what each one is FOR. Keyed on the
# management command, so the schedule below is read off CRONJOBS rather than
# retyped here: a cron entry that changes has to change what the page says, or
# the page becomes a plausible lie.
# label, what it does, and the key the manual-run endpoint knows it by
# (views.RUNNABLE_JOBS). The key is carried here so the button on the page and
# the whitelist on the server cannot drift apart.
#
# sync_mailable_counts is DELIBERATELY ABSENT. It fills a column in the Mining
# Matrix and nothing in Credit Control reads it; it only appeared here because
# this is where the HubSpot client happens to be exercised from. A job belongs
# on the page whose data it feeds, or nobody can find the button when the
# number looks wrong.
JOB_LABELS = {
    "refresh_credit_control": (
        "Routing", "Places new invoices and fires the day-4 handoff.", "routing",
        "credit_control_routing",
    ),
    "classify_invoice_types": (
        "Invoice type", "Decides Blind or Requested for speaker invoices.",
        "classifier", "credit_control_classifier",
    ),
    "sync_hubspot_calls": (
        "HubSpot calls", "Call counts and durations for the dashboard.", "hubspot",
        "hubspot_calls",
    ),
}


def schedule(*, now=None) -> list:
    """
    What runs when, when it last ran, and when it is next due.

    Read from settings.CRONJOBS through services.cron, which the Mining Matrix
    reads too, so the two pages cannot disagree about the same schedule.
    """
    from services import cron

    now = now or timezone.now()
    rows = []
    for command, (label, why, key, dataset) in JOB_LABELS.items():
        status = cron.job_status(command, dataset, now=now)
        if not status["cron"]:
            # Not scheduled on this deployment. Reported as such rather than
            # omitted, because a job nobody has scheduled is exactly the thing
            # somebody needs to notice.
            status["next_run"] = None
        rows.append({
            "job": label,
            "what": why,
            "key": key,
            "cron": status["cron"],
            "last_run": status["last_run"].isoformat() if status["last_run"] else None,
            "next_run": status["next_run"].isoformat() if status["next_run"] else None,
        })
    return sorted(
        rows,
        key=lambda r: (r["next_run"] is None, r["next_run"] or ""),
    )


def build(*, scoped_to=None, now=None) -> dict:
    """
    The whole dashboard payload.

    `scoped_to` is a user when the caller may only see their own rows, which
    keeps the page meaningful for an exec without leaking the team's totals.
    """
    now = now or timezone.now()
    groups = engine.status_group_map()
    roster = engine.Roster.load()

    active = list(
        CreditControlLead.objects
        .select_related("invoice", "assigned_to")
        .filter(bucket=CreditControlLead.Bucket.ACTIVE)
    )
    # Sponsors are chased by another team, so they are not ACTIVE and never
    # reach a caller's queue. They are still debt, and leaving them out of the
    # ageing table made it read as the whole book when it was only the phoned
    # part. Counted in the table, deliberately NOT in Total Active, which is a
    # figure about the calling queue.
    sponsors = list(
        CreditControlLead.objects
        .select_related("invoice")
        .filter(bucket=CreditControlLead.Bucket.SPEX)
    ) if scoped_to is None else []
    if scoped_to is not None:
        active = [lead for lead in active if lead.assigned_to_id == scoped_to.pk]

    people = roster.people()
    if scoped_to is not None:
        people = [p for p in people if p.pk == scoped_to.pk]

    def person_name(user):
        if not user:
            return "Unassigned"
        return user.get_full_name() or user.username

    h36, h72 = constants.AGE_HOUR_THRESHOLDS

    # ── per caller, and the group split ────────────────────────────────────
    status = {}
    for user in people:
        status[person_name(user)] = {
            "role": "Team lead" if roster.lead and user.pk == roster.lead.pk else "Exec",
            "total": 0, "h36": 0, "h72": 0,
            "groups": {g: {"total": 0, "h36": 0, "h72": 0} for g in constants.STATUS_GROUPS},
        }

    bifurcation = {
        g: {} for g in constants.STATUS_GROUPS
    }
    aged = {row: {label: 0 for label in _bucket_labels()} for row in AGED_ROWS}
    booking_codes: dict[str, int] = {}

    totals = {"total": 0, "h36": 0, "h72": 0, "flagged": 0, "unclassified": 0}

    for lead in active:
        invoice = lead.invoice
        hours = _age_hours(invoice.invoice_date, now)
        past36 = hours is not None and hours > h36
        past72 = hours is not None and hours > h72

        label = engine.normalize_disposition(lead.disposition)
        group = groups.get(label, constants.GROUP_NOT_ATTEMPTED) if label else constants.GROUP_NOT_ATTEMPTED
        key = label or "(not attempted)"

        totals["total"] += 1
        totals["h36"] += int(past36)
        totals["h72"] += int(past72)

        name = person_name(lead.assigned_to)
        row = status.get(name)
        if row is None:
            # Somebody outside the team still holds leads, most often after a
            # team change. Reported rather than dropped, or the sections would
            # stop reconciling and nobody would know why.
            row = status.setdefault(name, {
                "role": "Off team", "total": 0, "h36": 0, "h72": 0,
                "groups": {g: {"total": 0, "h36": 0, "h72": 0} for g in constants.STATUS_GROUPS},
            })
        row["total"] += 1
        row["h36"] += int(past36)
        row["h72"] += int(past72)
        cell = row["groups"].setdefault(group, {"total": 0, "h36": 0, "h72": 0})
        cell["total"] += 1
        cell["h36"] += int(past36)
        cell["h72"] += int(past72)

        slot = bifurcation.setdefault(group, {}).setdefault(
            key, {"total": 0, "h36": 0, "h72": 0},
        )
        slot["total"] += 1
        slot["h36"] += int(past36)
        slot["h72"] += int(past72)

        band = _bucket_for(lead.days_pending(now=now))
        if band:
            aged[_row_for(lead, invoice.booking_code)][band] += 1

        code = (invoice.booking_code or "Unspecified").strip() or "Unspecified"
        booking_codes[code] = booking_codes.get(code, 0) + 1

        if engine.is_speaker(invoice.booking_code) and not lead.effective_invoice_type:
            totals["unclassified"] += 1

    for lead in sponsors:
        band = _bucket_for(lead.days_pending(now=now))
        if band:
            aged["Sponsors"][band] += 1

    # Every seeded disposition renders even at zero, so the table's shape does
    # not change from day to day and a caller can find a row where they expect
    # it.
    for label, _category, group in constants.DISPOSITION_SEED:
        bifurcation.setdefault(group, {}).setdefault(label, {"total": 0, "h36": 0, "h72": 0})
    bifurcation.setdefault(constants.GROUP_NOT_ATTEMPTED, {}).setdefault(
        "(not attempted)", {"total": 0, "h36": 0, "h72": 0},
    )

    counts = dict(
        CreditControlLead.objects
        .values_list("bucket")
        .annotate(n=Count("invoice"))
        .values_list("bucket", "n")
    )

    resolved_week = CreditControlLead.objects.filter(
        bucket=CreditControlLead.Bucket.DONE,
        resolved=True,
        updated_at__gte=now - timedelta(days=7),
    ).count()

    return {
        "generated_at": now.isoformat(),
        "timezone": constants.DISPLAY_TZ,
        "kpis": {
            "total_active": totals["total"],
            "past_36h": totals["h36"],
            "past_72h": totals["h72"],
            "resolved_last_7_days": resolved_week,
            "not_invoiced": counts.get(CreditControlLead.Bucket.NOT_INVOICED, 0),
            "spex": counts.get(CreditControlLead.Bucket.SPEX, 0),
            "unclassified_speakers": totals["unclassified"],
        },
        "age_thresholds": {"h36": h36, "h72": h72},
        "status": status,
        "bifurcation": bifurcation,
        "aged_debtor": {
            "buckets": _bucket_labels(),
            "rows": aged,
            # Named rather than implied: this table now counts sponsors too, so
            # its total is the whole book and NOT the Total Active KPI above.
            # Two figures that differ by design need to say so.
            "active": totals["total"],
            "sponsors": len(sponsors),
        },
        "booking_codes": sorted(
            ({"code": c, "count": n} for c, n in booking_codes.items()),
            key=lambda r: (-r["count"], r["code"]),
        ),
        # Scoped with everything else. See shift_matrix: this section used to
        # ignore `scoped_to` and show every caller regardless of who asked.
        "shift_matrix": engine.shift_matrix(now=now, only=scoped_to),
        # What runs when, so nobody has to ask whether a figure is about to move.
        "schedule": schedule(now=now),
        "resolved": _resolved_attribution(now=now, scoped_to=scoped_to),
    }


def _resolved_attribution(*, now, scoped_to=None, days=30) -> dict:
    """
    Who collected what, over the last `days`.

    TWO NUMBERS PER CALLER, and the split is the point. A lead resolved after
    somebody worked it is that caller's win; a lead paid before anybody reached
    the contact is not, and rolling the two together would flatter the whole
    team. `resolved_after_effort` is stamped at the moment of payment, so this
    reports what was true on the day rather than what the row says now.
    """
    since = now - timedelta(days=days)
    rows = (
        CreditControlLead.objects
        .filter(bucket=CreditControlLead.Bucket.DONE, resolved_at__gte=since)
        .select_related("resolved_by")
    )
    if scoped_to is not None:
        rows = rows.filter(resolved_by=scoped_to)

    per_person: dict[str, dict] = {}
    totals = {"worked": 0, "unworked": 0, "total": 0}
    for lead in rows:
        user = lead.resolved_by
        name = (user.get_full_name() or user.username) if user else "Unassigned"
        cell = per_person.setdefault(name, {"worked": 0, "unworked": 0, "total": 0})
        key = "worked" if lead.resolved_after_effort else "unworked"
        cell[key] += 1
        cell["total"] += 1
        totals[key] += 1
        totals["total"] += 1

    return {
        "days": days,
        "totals": totals,
        "rows": sorted(
            ({"user": n, **v} for n, v in per_person.items()),
            key=lambda r: (-r["total"], r["user"]),
        ),
    }
