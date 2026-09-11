"""
services/cron.py
─────────────────
When a scheduled job last ran, and when it runs next.

SHARED, because two modules ask the same question about the same schedule.
Credit Control prints it for four jobs on its dashboard and the Mining Matrix
prints it beside the Mailable column, and the first version of this lived
inside Credit Control's dashboard module. A second copy in the matrix would
have been two answers to "when does this next happen", and they would have
drifted the first time somebody edited one cron string.

READ OFF settings.CRONJOBS, never retyped. A page that states a schedule it
does not read is a page that becomes a plausible lie the moment the schedule
changes, and a plausible lie about freshness is worse than saying nothing.

LAST RUN comes from book_event.SyncLog, which already records dataset, time,
status, row count and error for exactly this purpose. A job that writes no
SyncLog row simply reports None, and the caller shows only the next firing
rather than inventing a history.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

# A cron spec this module can read, expressed as the shapes this project
# actually uses. Anything outside them returns None and the UI says "see the
# schedule" rather than guessing, which is why there is no cron dependency
# here: a full parser would earn nothing against four specs.
#
#   "30 12 * * *"     one time of day
#   "0 * * * *"       every hour
#   "0 13-22 * * *"   every hour within a window
#   "30 1 * * *"      one time of day
_LOOKAHEAD_DAYS = 2


def next_fire(spec: str, now=None):
    """
    The next datetime `spec` fires, or None if it is not a shape we read.

    Walks candidate firings over the next two days and takes the first ahead of
    `now`. Two days rather than one because a job scheduled only at 01:30 has
    its next firing tomorrow once today's has passed.
    """
    now = now or timezone.now()
    try:
        minute, hour, dom, month, dow = spec.split()
    except (AttributeError, ValueError):
        return None
    if dom != "*" or month != "*" or dow != "*" or not minute.isdigit():
        return None

    if hour == "*":
        hours = list(range(24))
    elif hour.isdigit():
        hours = [int(hour)]
    elif "-" in hour:
        try:
            low, high = (int(part) for part in hour.split("-"))
        except ValueError:
            return None
        hours = list(range(low, high + 1))
    else:
        return None

    minute = int(minute)
    base = now.replace(second=0, microsecond=0)
    for day in range(_LOOKAHEAD_DAYS):
        for candidate_hour in hours:
            when = (base + timedelta(days=day)).replace(
                hour=candidate_hour, minute=minute,
            )
            if when > now:
                return when
    return None


def next_run_for(command: str, now=None):
    """
    When this management command next runs, across every entry that schedules it.

    The SOONEST, because a job scheduled both hourly and once with extra work
    (routing, which runs every hour and again with the HubSpot half before the
    shift) has one honest answer to "when does this next happen".
    """
    now = now or timezone.now()
    soonest = None
    for spec, _fn, args in getattr(settings, "CRONJOBS", []):
        if not args or args[0] != command:
            continue
        when = next_fire(spec, now)
        if when and (soonest is None or when < soonest):
            soonest = when
    return soonest


def specs_for(command: str) -> list:
    """Every cron spec that schedules this command, for a tooltip."""
    return [
        spec for spec, _fn, args in getattr(settings, "CRONJOBS", [])
        if args and args[0] == command
    ]


def last_run_for(dataset: str):
    """
    When the job that writes `dataset` last finished, or None.

    Imported inside the function: this module is read at request time by two
    apps, and importing a model at module scope would tie a small utility to
    the app registry being ready.
    """
    from book_event.models import SyncLog

    row = SyncLog.objects.filter(dataset=dataset).only("last_synced_at").first()
    return row.last_synced_at if row else None


def job_status(command: str, dataset: str | None = None, *, now=None) -> dict:
    """
    Both facts about one job, in the shape a page renders.

    `last` is None for a job that records no SyncLog row, and the caller shows
    only the next firing rather than pretending to know.
    """
    now = now or timezone.now()
    return {
        "command": command,
        "cron": ", ".join(specs_for(command)),
        "last_run": last_run_for(dataset) if dataset else None,
        "next_run": next_run_for(command, now),
    }
