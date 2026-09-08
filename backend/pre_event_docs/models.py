"""
pre_event_docs/models.py
─────────────────────────
The two things Pre-Event Docs cannot derive from anything else.

BadgeIssue      one badge, as it was PRINTED, on one badge run.
NetworkingPlan  one speed networking draw, frozen at the moment it was drawn.

EVERYTHING ELSE ON THIS PAGE IS A PROJECTION over book_delegates, computed per
request in services.py. These two are here because they record a decision a
person made, and a decision is not derivable from the current state of the data.

WHY BadgeIssue STORES THE PRINTED TEXT AS WELL AS THE DELEGATE
It replaces the workbook's Database_Sent tab, which held plain pasted values and
was therefore only ever as accurate as somebody's memory. The change lists are a
diff between what is on the badge table and what the booking says now, so a row
has to remember what was actually printed; reading the delegate's current name
would compare a value against itself and report no changes, ever.

The delegate FK is SET_NULL rather than CASCADE for the same reason. A badge that
was issued was issued, and the take out list still has to name whose badge to
pull off the table after the booking row itself is gone.

THE 14 DAY WINDOW IS NOT MEASURED FROM HERE
It is the EVENT start date minus 14 calendar days, a freeze date after which
badge changes are not actioned; see services.change_deadline. issued_at is still
stored and still NOT NULL, because a run has to be datable and undoable, but no
deadline is derived from it.

That is also what makes the workbook history importable. Its Database_Sent tab
holds 5,318 rows and the Stamp column is empty in every one of them, so a
badge-relative deadline could never have been computed for any of them.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

# Days BEFORE THE EVENT START DATE by which a badge change has to be actioned,
# counted in calendar days so weekends are included. Settings overridable for
# the same reason the booking code marker lists are, the rule is operational and
# can move without a code change. Applied in services.change_deadline.
DEFAULT_CHANGE_WINDOW_DAYS = 14


class BadgeIssue(models.Model):
    delegate = models.ForeignKey(
        "book_delegate.BookDelegate",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="badge_issues",
    )
    # Held alongside the FK, not read through it. The delegate may be gone, and
    # the desk still has to know which event's badge table this row belongs to.
    event_code = models.CharField(max_length=50, db_index=True)
    edition    = models.IntegerField(null=True, blank=True, db_index=True)

    # WHAT WAS PRINTED. Never updated after the run that wrote it.
    name    = models.CharField(max_length=255)
    company = models.CharField(max_length=255, blank=True, default="")

    # One badge run, one run_id, every badge in it sharing the value.
    #
    # WHY THIS COLUMN EXISTS. A log written by mistake does the same damage as a
    # log never written, and without a run grouping there is no way back from
    # one; the desk would have to delete rows by hand and guess which. With it,
    # undo is one filtered delete and the run history is one GROUP BY. Grouping
    # on equal issued_at instead would save the column and make run identity
    # depend on a timestamp collision, which is the implicit key that breaks.
    run_id = models.UUIDField(default=uuid.uuid4, db_index=True)

    issued_at = models.DateTimeField(default=timezone.now, db_index=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="badge_issues",
    )

    class Meta:
        db_table = "pre_event_docs_badge_issues"
        ordering = ["-issued_at", "-id"]
        indexes = [
            # The change detection lookup, every badge issued for one event,
            # newest first, so the latest row per delegate is the head of each
            # group rather than a sort over the whole table.
            models.Index(
                "event_code", "edition", models.F("issued_at").desc(),
                name="ped_badge_event_idx",
            ),
            models.Index(
                "delegate", models.F("issued_at").desc(),
                name="ped_badge_delegate_idx",
            ),
        ]

    def __str__(self):
        return f"{self.name} {self.event_code} {self.issued_at:%Y-%m-%d}"


class NetworkingPlan(models.Model):
    """
    One speed networking draw, stored rather than recomputed.

    WHY THIS IS STORED WHEN EVERY OTHER TAB IS NOT
    The draw is RANDOM, by requirement. A random result recomputed per request is
    a different result per request, so the table cards printed at nine o'clock
    would not match the screen at ten. Freezing it also means a delegate booking
    on the morning of the event does not silently reseat the whole room; they go
    into the next draw, which somebody chooses to make.

    Regenerating writes a NEW row rather than overwriting this one, so a draw
    that has already been printed stays readable after somebody asks for another.

    assignment holds the whole draw as one entry per attendee, tables in round
    order, which is what a table card needs. rosters() below reads it the other
    way round for whoever sets the room up. A seat table would be a second model
    and a join for data nothing ever filters on; a draw is written once and read
    whole. There is no seed column for the same reason, the answer is stored, so
    nothing ever needs to recompute the draw that produced it.
    """
    event_code = models.CharField(max_length=50, db_index=True)
    edition    = models.IntegerField(null=True, blank=True, db_index=True)

    # HOW MANY TABLES THE ROOM HAS. An input, honoured exactly.
    #
    # This was per_table, a target number of people per table, from which the
    # count was derived. That is backwards: a room has the tables it has, and
    # asking for six meant six PEOPLE per table, which on a 170 person event
    # produced 28 tables. The size of a table is now the consequence and the
    # count is the given, which is the way round the room works.
    tables = models.PositiveSmallIntegerField()
    rounds = models.PositiveSmallIntegerField(default=3)

    # Quality of the draw, and the bound it is judged against.
    #
    # BOTH are stored, because `optimal` cannot be recomputed from repeat_pairs
    # alone. Eighteen people at tables of six force eighteen repeats whatever the
    # seating, so a saved draw showing eighteen is either perfect or poor
    # depending entirely on the floor, and without the floor beside it the page
    # has to guess. See networking.floor_repeats.
    repeat_pairs  = models.PositiveIntegerField(default=0)
    floor_repeats = models.PositiveIntegerField(default=0)
    assignment    = models.JSONField(default=list)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="networking_plans",
    )

    class Meta:
        db_table = "pre_event_docs_networking_plans"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                "event_code", "edition", models.F("created_at").desc(),
                name="ped_plan_event_idx",
            ),
        ]

    def __str__(self):
        return (f"{self.event_code} draw {self.created_at:%Y-%m-%d %H:%M}, "
                f"{self.attendees} people, {self.repeat_pairs} repeats")

    @property
    def attendees(self):
        return len(self.assignment or [])

    @property
    def largest_table(self):
        """
        How many people sit at the busiest table.

        The figure somebody laying out a room needs, and the one that used to be
        an input under the name per_table. Derived, because the assignment is
        stored whole and counting it is exact where a stored target was a wish.
        """
        if not self.assignment or not self.tables:
            return 0
        counts = [0] * (self.tables + 1)
        for entry in self.assignment:
            first = (entry.get("tables") or [None])[0]
            if isinstance(first, int) and 0 < first <= self.tables:
                counts[first] += 1
        return max(counts)

    @property
    def optimal(self):
        """
        True when this draw is provably the best the table plan allows.

        Read this rather than `repeat_pairs == 0`. In a room too small to avoid
        repeats, zero is not available and a draw at the floor is the best
        matchup there is.
        """
        return self.repeat_pairs <= self.floor_repeats

    def rosters(self):
        """
        The draw turned inside out, one roster per table per round.

        The assignment is per PERSON, which is what an attendee needs on a card.
        Whoever sets the room up needs the reverse, who is sitting at table four
        in round two, and that is this. Computed rather than stored, because it
        is the same data read the other way round.
        """
        rounds = [{} for _ in range(self.rounds)]
        for entry in self.assignment or []:
            for index, table in enumerate(entry.get("tables") or []):
                if index < len(rounds):
                    rounds[index].setdefault(table, []).append(entry)
        return [
            {
                table: sorted(
                    people,
                    key=lambda p: ((p.get("company") or ""), (p.get("name") or "")),
                )
                for table, people in sorted(round_map.items())
            }
            for round_map in rounds
        ]
