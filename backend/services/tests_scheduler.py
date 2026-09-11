"""
Does the scheduler fire each job once, at its time, and never replay?

The thread, the flock and call_command are not tested. What is worth testing is
`due()`, because it is the whole decision and every failure mode is silent: a
job that fires twice spends twice at Anthropic, a job that never advances fires
on every tick for the rest of the day, and a restart that replays the morning
looks exactly like working software until the bill arrives.
"""
from datetime import timedelta

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from services import scheduler

HOURLY = ("0 * * * *", "django.core.management.call_command", ["routing"])
DAILY = ("30 12 * * *", "django.core.management.call_command", ["contacts"])
WINDOW = ("0 13-22 * * *", "django.core.management.call_command", ["calls"])
UNREADABLE = ("*/5 * * * *", "django.core.management.call_command", ["stepped"])


def at(hour, minute=0):
    """An aware UTC instant on a fixed day, so the maths is not clock dependent."""
    return timezone.now().replace(
        year=2026, month=9, day=11, hour=hour, minute=minute,
        second=0, microsecond=0,
    )


@override_settings(CRONJOBS=[HOURLY, DAILY, WINDOW])
class DueTests(SimpleTestCase):

    def test_nothing_is_due_at_startup(self):
        """
        A redeploy must not replay the day. `plan` primes every entry with its
        NEXT firing, so the first tick after a restart runs nothing.
        """
        now = at(14, 5)
        entries = scheduler.plan(now)
        self.assertEqual(len(entries), 3)
        self.assertEqual(scheduler.due(entries, now), [])

    def test_a_job_fires_once_and_then_waits_for_the_next_slot(self):
        entries = scheduler.plan(at(14, 5))

        # 15:00, the hourly and the windowed job are both due, the daily is not.
        fired = scheduler.due(entries, at(15, 0))
        self.assertEqual(sorted(fired), [["calls"], ["routing"]])

        # The very next tick, thirty seconds later, must run nothing at all.
        self.assertEqual(scheduler.due(entries, at(15, 0) + timedelta(seconds=30)), [])

        # And the hour after, both again, exactly once.
        self.assertEqual(sorted(scheduler.due(entries, at(16, 0))), [["calls"], ["routing"]])

    def test_a_job_missed_by_a_slow_tick_still_fires(self):
        """
        Jobs run serially, so a long one can push the next tick past another
        job's minute. Late is correct; skipped is not.
        """
        entries = scheduler.plan(at(12, 0))
        self.assertEqual(scheduler.due(entries, at(12, 44)), [["contacts"]])

    def test_an_entry_that_raises_is_still_advanced_by_due(self):
        """
        `due` advances before the caller runs anything, so a command that blows
        up is retried at its next slot rather than on every tick until midnight.
        """
        entries = scheduler.plan(at(14, 5))
        scheduler.due(entries, at(15, 0))
        hourly = next(e for e in entries if e["args"] == ["routing"])
        self.assertEqual(hourly["due"], at(16, 0))


@override_settings(CRONJOBS=[WINDOW])
class WindowedJobTests(SimpleTestCase):
    """
    The callers work 13:00 to 22:00 UTC, so the HubSpot job is scheduled hourly
    only through the shift. Its own class, because tested alongside the hourly
    and daily entries every assertion would be about all three.
    """

    def test_it_fires_every_hour_in_the_window_and_then_waits_for_tomorrow(self):
        entries = scheduler.plan(at(12, 0))
        self.assertEqual(entries[0]["due"], at(13, 0))

        for hour in range(13, 23):
            self.assertEqual(
                scheduler.due(entries, at(hour, 0)), [["calls"]],
                f"the {hour}:00 firing is inside the shift and must run",
            )

        # 22:00 was the last one. The next is tomorrow's 13:00, so the six hours
        # of the night in between run nothing.
        self.assertEqual(entries[0]["due"], at(13, 0) + timedelta(days=1))
        self.assertEqual(scheduler.due(entries, at(23, 30)), [])


@override_settings(CRONJOBS=[HOURLY, UNREADABLE])
class UnreadableSpecTests(SimpleTestCase):

    def test_a_spec_this_module_cannot_read_is_dropped_not_guessed(self):
        """
        services.cron reads the four shapes this project uses. A stepped spec is
        not one of them, and running it at the wrong time would be worse than
        not running it, so it is dropped and logged.
        """
        with self.assertLogs("services.scheduler", level="WARNING") as logged:
            entries = scheduler.plan(at(14, 5))
        self.assertEqual([e["args"] for e in entries], [["routing"]])
        self.assertIn("unreadable cron spec", logged.output[0])


WEEKLY = ("0 2 * * 0", "django.core.management.call_command", ["prune"])


@override_settings(CRONJOBS=[WEEKLY])
class WeeklyJobTests(SimpleTestCase):
    """
    The log prune runs Sunday 02:00. It was being dropped, because next_fire
    refused any day-of-week spec and only looked two days ahead. Nothing noticed
    while the schedule was merely being PRINTED; a runner reading the same list
    turns that into a job that never runs.
    """

    def test_a_weekly_job_is_planned_for_its_day_not_dropped(self):
        # 11 September 2026 is a Friday, so the next Sunday 02:00 is the 13th.
        entries = scheduler.plan(at(14, 0))
        self.assertEqual(len(entries), 1, "the weekly spec must be readable")
        self.assertEqual(entries[0]["due"], at(2, 0) + timedelta(days=2))
        self.assertEqual(entries[0]["due"].weekday(), 6)

    def test_it_fires_once_then_waits_a_full_week(self):
        entries = scheduler.plan(at(14, 0))
        sunday = at(2, 0) + timedelta(days=2)

        self.assertEqual(scheduler.due(entries, sunday), [["prune"]])
        self.assertEqual(scheduler.due(entries, sunday + timedelta(hours=1)), [])
        self.assertEqual(entries[0]["due"], sunday + timedelta(days=7))

    def test_cron_counts_sunday_as_both_0_and_7(self):
        """Real crontabs use either, and a day's drift would be invisible."""
        from services import cron

        friday = at(14, 0)
        self.assertEqual(
            cron.next_fire("0 2 * * 0", friday), cron.next_fire("0 2 * * 7", friday),
        )
        # And a weekday spec lands on that weekday, not on Sunday.
        wednesday = cron.next_fire("0 2 * * 3", friday)
        self.assertEqual(wednesday.weekday(), 2)
