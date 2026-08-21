"""
accounts/tests_reporting_manager.py
───────────────────────────────────
`User.mapped_lead` — "the specific team lead this user/member is mapped under" —
is the reporting manager. The column and both serializer halves existed from the
start, but nothing in the UI read or wrote them, so the field was invisible,
unsettable, and null on all 46 live users.

What is worth holding still:
  * the read shape is the same for every row, present or absent. A dotted
    ReadOnlyField whose traversal hits None is DROPPED from the payload unless
    allow_null says otherwise, and a key that is sometimes missing is a key every
    caller has to guard.
  * an explicit null CLEARS it. This is the bug the sentinel in update() exists
    for: popping with a None default made "field absent" and "field null" the
    same value, so neither a reporting manager nor a team could ever be
    unassigned — the request answered 200 and changed nothing.
  * nobody reports to themselves, however the request is shaped.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from teams.models import Team

User = get_user_model()


class ReportingManagerAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_team = Team.objects.create(name="Admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="hp", password="x", email="hp@iq-hub.com", team=cls.admin_team,
        )

        cls.sales = Team.objects.create(name="Sales Team")
        cls.primary = User.objects.create_user(
            username="tt", password="x", email="tt@iq-hub.com",
            first_name="Terry", last_name="Tamayo",
            team=cls.sales, is_team_lead=True,
        )
        cls.sales.team_lead = cls.primary
        cls.sales.save()
        cls.second = User.objects.create_user(
            username="fc", password="x", email="fc@iq-hub.com",
            first_name="Fred", last_name="Carrasco",
            team=cls.sales, is_team_lead=True,
        )
        cls.member = User.objects.create_user(
            username="rd", password="x", email="rd@iq-hub.com",
            first_name="Rick", last_name="Delacruz", team=cls.sales,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _row(self, user):
        resp = self.client.get("/api/users/", {"page_size": 100})
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        return next(r for r in rows if r["username"] == user.username)

    def test_the_keys_are_present_even_when_nobody_is_mapped(self):
        row = self._row(self.member)
        self.assertIn("mapped_lead_id", row)
        self.assertIn("mapped_lead_name", row)
        self.assertIsNone(row["mapped_lead_id"])
        self.assertIsNone(row["mapped_lead_name"])

    def test_it_can_be_set_and_reads_back_by_name(self):
        resp = self.client.patch(
            f"/api/users/{self.member.id}/", {"mapped_lead_id": self.primary.id}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        self.member.refresh_from_db()
        self.assertEqual(self.member.mapped_lead_id, self.primary.id)

        row = self._row(self.member)
        self.assertEqual(row["mapped_lead_id"], self.primary.id)
        self.assertEqual(row["mapped_lead_name"], "Terry Tamayo")

    def test_a_second_lead_can_be_the_manager(self):
        """A team may have any number of leads; none of them is a special case."""
        resp = self.client.patch(
            f"/api/users/{self.member.id}/", {"mapped_lead_id": self.second.id}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.member.refresh_from_db()
        self.assertEqual(self.member.mapped_lead_id, self.second.id)

    def test_an_explicit_null_clears_it(self):
        """
        THE REGRESSION TEST FOR THE UNCLEARABLE-FK BUG. Before the _MISSING
        sentinel this answered 200 and left the old manager in place.
        """
        self.member.mapped_lead = self.primary
        self.member.save()

        resp = self.client.patch(
            f"/api/users/{self.member.id}/", {"mapped_lead_id": None}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        self.member.refresh_from_db()
        self.assertIsNone(
            self.member.mapped_lead_id,
            "an explicit null must unassign — allow_null=True on the field says "
            "so, and the form sends exactly this to clear the box",
        )

    def test_an_absent_field_leaves_it_alone(self):
        """The other half of the sentinel: absent is not null."""
        self.member.mapped_lead = self.primary
        self.member.save()

        resp = self.client.patch(
            f"/api/users/{self.member.id}/", {"first_name": "Ricky"}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        self.member.refresh_from_db()
        self.assertEqual(self.member.first_name, "Ricky")
        self.assertEqual(
            self.member.mapped_lead_id, self.primary.id,
            "a PATCH that never mentioned the manager must not clear it",
        )

    def test_a_team_can_be_unassigned_too(self):
        """Same bug, same fix, the field next door."""
        resp = self.client.patch(
            f"/api/users/{self.member.id}/", {"team_id": None}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.member.refresh_from_db()
        self.assertIsNone(self.member.team_id)

    def test_an_administrator_can_be_the_recorded_manager(self):
        """
        A team lead has no lead above them, so the only person they can report to
        is an administrator. Nothing about the write path is special-cased to
        team leads, and this pins that down: the FK is to `self`, not to a lead.
        """
        resp = self.client.patch(
            f"/api/users/{self.primary.id}/", {"mapped_lead_id": self.admin.id}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        self.primary.refresh_from_db()
        self.assertEqual(self.primary.mapped_lead_id, self.admin.id)
        self.assertEqual(self._row(self.primary)["mapped_lead_name"], "hp")

    def test_the_admin_team_is_identifiable_from_the_payload(self):
        """
        The UI picks administrators out of the users list by role or by the
        all-access team, so both signals have to survive serialization — without
        them the reporting-manager choices lose the whole Administrators group.
        """
        row = self._row(self.admin)
        self.assertTrue(
            row["role"] == "admin" or row["has_all_access"],
            f"neither signal present on an admin row: {row.get('role')!r} / "
            f"{row.get('has_all_access')!r}",
        )

    def test_nobody_reports_to_themselves(self):
        resp = self.client.patch(
            f"/api/users/{self.member.id}/", {"mapped_lead_id": self.member.id}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.member.refresh_from_db()
        self.assertIsNone(self.member.mapped_lead_id)
