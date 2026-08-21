"""
events/tests_owner_resolution.py
────────────────────────────────
Six of the seven owner columns on Event are blank on EVERY row of the live data —
only sales_team, the SCA, was ever populated — so the drawer's Teams tab and the
Events table rendered six empty rows on every event. Blank ones now resolve to
the lead of the team that owns the role.

What is worth holding still:
  * a value stored on the EVENT always wins, and never appears as inherited;
  * the fallback is keyed on the team NAME's implied role, so it survives a
    rename that keeps the keyword and picks up a new team without a second map;
  * the columns deliberately left out stay out — a fallback on
    market_research_junior would print the same name as the senior column, and
    nothing in the Teams module owns event_management_team;
  * it costs ONE query per response, not one per event. This serializer is walked
    a page at a time over ~200 events, and a per-event team lookup would be
    invisible in a test that only checked the values.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from events.models import Event
from events.serializers import EventListSerializer, OWNER_ROLE_SOURCES
from teams.models import Team

User = get_user_model()


class OwnerResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def lead(username, first, last, team_name):
            u = User.objects.create_user(username=username, password="x",
                                         first_name=first, last_name=last)
            t = Team.objects.create(name=team_name, team_lead=u)
            return u, t

        cls.sales_lead, cls.sales = lead("tt", "Terry", "Tamayo", "Sales Team")
        cls.tele_lead, cls.tele = lead("by", "Bruce", "Yanez", "Tele Marketing Team")
        cls.mr_lead, cls.mr = lead("vv", "Vick", "Varela", "Market Research Team")
        cls.spex_lead, cls.spex = lead("vr", "Vince", "Rojas", "SpEx Team")

    # A real date object, not "2026-04-22": Event.event_status compares
    # event_date against today, so a string survives create() and then blows up
    # inside serialization with a TypeError that names neither.
    EVENT_DATE = date(2026, 4, 22)

    def _resolution(self, **fields):
        ev = Event.objects.create(event_code="AFU - AD", event_date=self.EVENT_DATE, **fields)
        return EventListSerializer(ev).data["owner_resolution"]

    def test_blank_columns_resolve_to_the_owning_teams_lead(self):
        got = self._resolution()
        self.assertEqual(
            {k: v["name"] for k, v in got.items()},
            {
                "team_leader": "Terry Tamayo",
                "telemarketing_team": "Bruce Yanez",
                "market_research_senior": "Vick Varela",
                "spex_team": "Vince Rojas",
            },
        )
        self.assertEqual(got["telemarketing_team"]["team"], "Tele Marketing Team")

    def test_a_value_on_the_event_wins_and_is_not_reported_as_inherited(self):
        got = self._resolution(team_leader="Someone Specific")
        self.assertNotIn(
            "team_leader", got,
            "a column with its own value must not appear as inherited — the UI "
            "labels everything in this dict as belonging to the team",
        )
        self.assertIn("spex_team", got, "the other blank columns still resolve")

    def test_whitespace_is_blank(self):
        """
        Imported columns hold whitespace where a human meant nothing. Treating
        "   " as a value shows an empty cell and suppresses the fallback.
        """
        self.assertIn("spex_team", self._resolution(spex_team="   "))

    def test_the_deliberately_unwired_columns_stay_unwired(self):
        got = self._resolution()
        for field in ("sales_team", "market_research_junior", "event_management_team"):
            self.assertNotIn(field, got, f"{field} is not team-backed on purpose")

    def test_a_renamed_team_still_answers_while_it_keeps_the_keyword(self):
        self.tele.name = "Tele Marketing Team (EMEA)"
        self.tele.save()
        self.assertEqual(
            self._resolution()["telemarketing_team"]["name"], "Bruce Yanez",
        )

    def test_a_team_with_no_lead_contributes_nothing(self):
        self.spex.team_lead = None
        self.spex.save()
        self.assertNotIn("spex_team", self._resolution())

    def test_an_archived_team_is_ignored(self):
        self.mr.is_archived = True
        self.mr.save()
        self.assertNotIn("market_research_senior", self._resolution())

    def test_lowest_pk_wins_when_two_teams_imply_one_role(self):
        """
        Without a tie-break the answer follows whatever order the database
        returned and can differ between two requests for the same event.
        """
        other = User.objects.create_user(username="zz", password="x",
                                         first_name="Zoe", last_name="Zane")
        Team.objects.create(name="Inside Sales", team_lead=other)
        self.assertEqual(self._resolution()["team_leader"]["name"], "Terry Tamayo")

    def test_one_query_however_many_events(self):
        for i in range(12):
            Event.objects.create(event_code=f"E{i}", event_date=self.EVENT_DATE)

        # EventViewSet.get_queryset's exact shape, materialised BEFORE the count
        # starts. Without the prefetch, assigned_sales_users costs a query per
        # event and buries the number this test exists to pin down.
        events = list(
            Event.objects.select_related("sales_executive").prefetch_related("assigned_users")
        )

        with CaptureQueriesContext(connection) as ctx:
            data = EventListSerializer(events, many=True).data

        self.assertEqual(len(data), 12)
        self.assertEqual(
            len(ctx.captured_queries), 1,
            "the team lookup must be memoised on the shared child serializer; "
            f"{len(ctx.captured_queries)} queries for 12 events means it is not",
        )

    def test_every_wired_column_is_a_real_event_field(self):
        names = {f.name for f in Event._meta.get_fields()}
        for field in OWNER_ROLE_SOURCES:
            self.assertIn(field, names, f"{field} is not a column on Event")
