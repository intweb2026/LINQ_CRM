"""
services/scheduler.py
──────────────────────
Runs settings.CRONJOBS inside the web process.

WHY NOT django-crontab, WHICH IS STILL INSTALLED. It writes the system crontab,
and four things have to be true for that to fire here. Cron has to be in the
image, its daemon has to be running, `manage.py crontab add` has to be re-run
after every redeploy because Coolify replaces the container, and the job needs
an environment. The last one is what actually bit. A cron child is handed a
minimal environment, and this project reads its configuration from the process
environment the platform injects; settings.py loads a .env that is gitignored
and therefore absent from the image. So the job did start, settings failed to
import on a missing SECRET_KEY, and the traceback went to a mail spool that does
not exist. Silence, an empty SyncLog, and a dashboard reporting "never run".

A thread in the web process has none of those problems. It already holds the
environment, the settings and the database, because it is the app.

ONE SOURCE OF TRUTH. Specs are read from settings.CRONJOBS through services.cron,
which is what the Credit Control dashboard and the Mining Matrix already print.
A schedule the page states and the runner ignores is precisely the lie this
module exists to prevent, so it reads the same list rather than keeping its own.

ponytail: one thread, jobs serial, one container. A long classifier run delays
whatever is queued behind it by its own duration, which is minutes at worst and
costs nothing at this volume. The lock below is per container, so move to a real
job runner if this app is ever served by two.
"""
from __future__ import annotations

import logging
import threading
import time

from django.conf import settings
from django.utils import timezone

from . import cron

logger = logging.getLogger(__name__)

# Specs have minute granularity, so half a minute means a job fires within
# thirty seconds of its time and no firing can be stepped over.
TICK_SECONDS = 30

_started = False
# Holds the lock file open for the life of the process. A closed file drops the
# flock with it, which would let a second worker start a second scheduler.
_lock_handle = []


def plan(now) -> list:
    """
    Every readable CRONJOBS entry, paired with its next firing.

    Primed from `now`, so a restart never replays what was scheduled while the
    process was down. Without that, a redeploy mid-afternoon would fire the
    whole morning at once, including the jobs that spend money.
    """
    entries = []
    for spec, _fn, args in getattr(settings, "CRONJOBS", []):
        when = cron.next_fire(spec, now)
        if when is None:
            # Reported, not skipped silently. services.cron reads the four spec
            # shapes this project uses; a fifth one added later has to be
            # noticed rather than quietly never run.
            logger.warning("scheduler: unreadable cron spec %r for %s", spec, args)
            continue
        entries.append({"spec": spec, "args": list(args), "due": when})
    return entries


def due(entries, now) -> list:
    """
    The commands to run at `now`, advancing each entry past this firing.

    Pure, and the only decision this module makes, which is why it is the part
    with a test. Advancing BEFORE the command runs is deliberate: a job that
    raises must not be retried on every tick for the rest of the day.
    """
    ready = []
    for entry in entries:
        if entry["due"] is None or now < entry["due"]:
            continue
        entry["due"] = cron.next_fire(entry["spec"], now)
        ready.append(entry["args"])
    return ready


def _run(args) -> None:
    """
    Run one management command. Never raises.

    One job failing must not take the scheduler down with it, or the first
    HubSpot outage would silently stop routing too.
    """
    from django.core.management import call_command
    from django.db import connections

    started = timezone.now()
    try:
        call_command(*args)
        elapsed = (timezone.now() - started).total_seconds()
        logger.info("scheduler: %s finished in %.1fs", " ".join(args), elapsed)
    except Exception:  # noqa: BLE001
        logger.exception("scheduler: %s failed", " ".join(args))
    finally:
        # This thread lives for the life of the process and is idle between
        # firings. A connection held across that gap is one the database counts
        # against max_connections while nobody is using it, and one that has
        # usually been dropped by the server before the next job wants it.
        connections.close_all()


def _loop() -> None:
    entries = plan(timezone.now())
    logger.info(
        "scheduler: running, %d job(s), next at %s",
        len(entries), min((e["due"] for e in entries), default=None),
    )
    while True:
        # Sleep first. Nothing is due at startup by construction, and waking to
        # do nothing is the honest shape of the loop.
        time.sleep(TICK_SECONDS)
        try:
            for args in due(entries, timezone.now()):
                _run(args)
        except Exception:  # noqa: BLE001
            logger.exception("scheduler: tick failed")


def _claim() -> bool:
    """
    True in exactly one process per container.

    gunicorn is started without -w and so runs a single worker today, but a
    worker count is one flag away from changing, and two schedulers would double
    every job, including the ones that spend money at Anthropic. An advisory
    file lock costs six lines and removes the question permanently.
    """
    try:
        import fcntl
    except ImportError:
        # Windows development, where this is a single process by definition.
        return True
    handle = open("/tmp/linq-scheduler.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _lock_handle.append(handle)
    return True


def start() -> None:
    """
    Start the scheduler, once, if this deployment wants one.

    Called from wsgi.py and asgi.py rather than from an AppConfig.ready(),
    because ready() also runs for `migrate`, `shell`, `test` and every other
    management command. A migration is not a thing that should start firing
    HubSpot syncs, and a test run is not a thing that should call Anthropic.
    """
    global _started
    if _started or not getattr(settings, "RUN_SCHEDULER", False):
        return
    if not _claim():
        logger.info("scheduler: another worker holds the lock, not starting")
        return
    _started = True
    threading.Thread(target=_loop, name="cronjobs", daemon=True).start()
