"""
teams/tests_multi_lead.py
─────────────────────────
A team may have ANY NUMBER of leads, on every team, not just the one that
happened to have two in the imported data.

Two sources record the answer and both matter:
  * Team.team_lead — the single PRIMARY lead, for everywhere the app expects one;
  * User.is_team_lead — the flag every lead carries, including the primary.

assign-lead owns both. It clears the flag across the team and reapplies it from
the payload, which is correct only if the payload is the COMPLETE list — the
frontend used to send one id, so saving demoted every other lead in silence.
These tests pin the list-shaped contract, the primary sync, and the demotion
being a consequence of what was sent rather than a side effect of the endpoint.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from teams.models import Team, TeamActivityLog

User = get_user_model()


class MultiLeadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="hp", password="x", email="hp@iq-hub.com",
            team=Team.objects.create(name="Admin", is_all_access=True),
        )
        # A team with no imported leads, to prove this is not Sales-specific.
        cls.team = Team.objects.create(name="SpEx Team")
        cls.a, cls.b, cls.c = (
            User.objects.create_user(
                username=u, password="x", email=f"{u}@iq-hub.com",
                first_name=f.split()[0], last_name=f.split()[1], team=cls.team,
            )
            for u, f in (("vr", "Vince Rojas"), ("aa", "Ann Ng"), ("bb", "Bo Li"))
        )
        cls.outsider = User.objects.create_user(
            username="zz", password="x", email="zz@iq-hub.com",
            first_name="Zoe", last_name="Zane",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _assign(self, *users):
        resp = self.client.post(
            f"/api/teams/{self.team.id}/assign-lead/",
            {"user_ids": [u.id for u in users]}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.team.refresh_from_db()
        return resp

    def _flagged(self):
        return set(
            User.objects.filter(team=self.team, is_team_lead=True)
            .values_list("username", flat=True)
        )

    def test_three_leads_on_a_team_that_had_none(self):
        self._assign(self.a, self.b, self.c)
        self.assertEqual(self._flagged(), {"vr", "aa", "bb"})
        self.assertEqual(
            self.team.team_lead_id, self.a.id,
            "the first id in the payload is the primary and lands on the FK",
        )

    def test_the_primary_is_whichever_id_came_first(self):
        self._assign(self.c, self.a)
        self.assertEqual(self.team.team_lead_id, self.c.id)
        self.assertEqual(self._flagged(), {"bb", "vr"})

    def test_the_serializer_reports_every_lead(self):
        self._assign(self.a, self.b, self.c)
        resp = self.client.get(f"/api/teams/{self.team.id}/")
        self.assertEqual(resp.status_code, 200, resp.content)

        self.assertEqual(
            {l["name"] for l in resp.data["team_leads"]},
            {"Vince Rojas", "Ann Ng", "Bo Li"},
            "team_leads is what the UI reads to show more than one lead",
        )
        self.assertEqual(resp.data["team_lead_name"], "Vince Rojas")

    def test_resending_a_shorter_list_demotes_the_ones_left_out(self):
        """
        The endpoint replaces rather than merges, so this is correct — but it is
        also exactly why the caller must send the whole list. A form that offered
        one choice made this a silent demotion of everyone else.
        """
        self._assign(self.a, self.b, self.c)
        self._assign(self.a, self.b)
        self.assertEqual(self._flagged(), {"vr", "aa"})
        self.assertEqual(self.team.team_lead_id, self.a.id)

    def test_an_untouched_resend_of_the_same_list_changes_nothing(self):
        """What the modal does when someone opens it and saves without editing."""
        self._assign(self.a, self.b)
        before = (self._flagged(), self.team.team_lead_id)
        self._assign(self.a, self.b)
        self.assertEqual((self._flagged(), self.team.team_lead_id), before)

    def test_an_empty_list_removes_every_lead(self):
        self._assign(self.a, self.b)
        resp = self.client.post(
            f"/api/teams/{self.team.id}/assign-lead/", {"user_ids": []}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.team.refresh_from_db()
        self.assertEqual(self._flagged(), set())
        self.assertIsNone(self.team.team_lead_id)

    def test_a_non_member_cannot_be_made_a_lead(self):
        self._assign(self.a)
        resp = self.client.post(
            f"/api/teams/{self.team.id}/assign-lead/",
            {"user_ids": [self.outsider.id]}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.outsider.refresh_from_db()
        self.team.refresh_from_db()
        self.assertFalse(self.outsider.is_team_lead)
        self.assertIsNone(
            self.team.team_lead_id,
            "an id that is not a member of the team contributes no lead",
        )

    def test_leads_on_one_team_are_untouched_by_another_teams_assignment(self):
        """The flag is global on the user, so the clear must be team-scoped."""
        other = Team.objects.create(name="Tele Marketing Team")
        member = User.objects.create_user(
            username="by", password="x", email="by@iq-hub.com",
            first_name="Bruce", last_name="Yanez", team=other,
        )
        resp = self.client.post(
            f"/api/teams/{other.id}/assign-lead/", {"user_ids": [member.id]}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        self._assign(self.a, self.b)

        member.refresh_from_db()
        self.assertTrue(
            member.is_team_lead,
            "assigning SpEx's leads must not demote Tele Marketing's",
        )

    def test_the_activity_log_names_every_lead(self):
        self._assign(self.a, self.b, self.c)
        note = (
            TeamActivityLog.objects
            .filter(team=self.team, action_type=TeamActivityLog.ActionType.LEAD_ASSIGNED)
            .latest("created_at").notes
        )
        for username in ("vr", "aa", "bb"):
            self.assertIn(username, note, note)

    def test_the_single_id_form_still_works(self):
        """Kept for older callers; it means a team with exactly one lead."""
        resp = self.client.post(
            f"/api/teams/{self.team.id}/assign-lead/",
            {"user_id": self.b.id}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.team.refresh_from_db()
        self.assertEqual(self._flagged(), {"aa"})
        self.assertEqual(self.team.team_lead_id, self.b.id)
