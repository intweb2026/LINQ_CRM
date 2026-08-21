"""
teams/tests_team_details.py
────────────────────────────
The edit-team-details form writes four things through two endpoints, and two of
them had never been exercised from the API at all.

  * `is_all_access` sat WRITABLE on a PATCH gated on `teams.update`. Anyone who
    could rename their own team could also hand it every module in the grid,
    bypassing the permissions endpoint that is gated on `roles` precisely because
    that decision is the dangerous one. Two tests below pin both halves: refused
    for a team editor, allowed for someone holding `roles.update`.
  * `is_archived` through the same PATCH, which the board's Archived checkbox
    uses, and which has to leave an activity entry — archiving takes a whole
    column off the board.
  * assign-lead's multi-lead form returned leads in whatever order the database
    felt like, so `team_lead`, taken as leads[0], was not the one the caller put
    first. The form states that the first ticked member is the primary lead; that
    is only true if the order survives the round trip.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from teams.models import Team, TeamActivityLog, TeamPermission

User = get_user_model()


def grant(team, module, **cells):
    """One permission row. Anything unnamed is denied, as a missing row would be."""
    return TeamPermission.objects.create(
        team=team, module=module,
        can_view=cells.get("view", False), can_create=cells.get("create", False),
        can_update=cells.get("update", False), can_delete=cells.get("delete", False),
    )


class TeamDetailsWriteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # A team editor: teams.update, and nothing on roles. Named without any of
        # the keywords User.save() reads, so the role stays what it is set to.
        cls.editors = Team.objects.create(name="Coordination Desk")
        grant(cls.editors, "teams", view=True, update=True)
        cls.editor = User.objects.create_user(
            username="td_editor", password="x", role="operations",
            email="td_editor@iq-hub.com", team=cls.editors,
        )

        # Same, plus the right the permissions endpoint demands.
        cls.grid_keepers = Team.objects.create(name="Grid Desk")
        grant(cls.grid_keepers, "teams", view=True, update=True)
        grant(cls.grid_keepers, "roles", view=True, update=True)
        cls.grid_keeper = User.objects.create_user(
            username="td_roles", password="x", role="operations",
            email="td_roles@iq-hub.com", team=cls.grid_keepers,
        )

    def setUp(self):
        self.client = APIClient()
        self.subject = Team.objects.create(name="Field Desk", color="#111111")

    def as_editor(self):
        self.client.force_authenticate(user=self.editor)

    def as_grid_keeper(self):
        self.client.force_authenticate(user=self.grid_keeper)

    # ── The plain details ───────────────────────────────────────────────────

    def test_a_team_editor_can_rename_recolour_and_describe(self):
        self.as_editor()
        resp = self.client.patch(
            f"/api/teams/{self.subject.id}/",
            {"name": "Field Desk North", "color": "#222222", "description": "Regional"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.subject.refresh_from_db()
        self.assertEqual(self.subject.name, "Field Desk North")
        self.assertEqual(self.subject.color, "#222222")
        self.assertEqual(self.subject.description, "Regional")
        self.assertTrue(
            TeamActivityLog.objects.filter(
                team=self.subject,
                action_type=TeamActivityLog.ActionType.TEAM_RENAMED,
            ).exists(),
            "a rename left no activity entry",
        )

    def test_the_slug_is_left_alone_by_a_rename(self):
        """
        Renaming must not renumber URLs. `slug` is unique and only filled when
        blank, so a rename keeps the one it was created with.
        """
        original = self.subject.slug
        self.as_editor()
        self.client.patch(f"/api/teams/{self.subject.id}/",
                          {"name": "Field Desk South"}, format="json")
        self.subject.refresh_from_db()
        self.assertEqual(self.subject.slug, original)

    # ── Archived ────────────────────────────────────────────────────────────

    def test_archiving_through_the_details_patch_is_recorded(self):
        self.as_editor()
        resp = self.client.patch(f"/api/teams/{self.subject.id}/",
                                 {"is_archived": True}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.subject.refresh_from_db()
        self.assertTrue(self.subject.is_archived)
        self.assertTrue(
            TeamActivityLog.objects.filter(
                team=self.subject,
                action_type=TeamActivityLog.ActionType.TEAM_ARCHIVED,
            ).exists(),
            "archiving left no activity entry",
        )

    def test_an_archived_team_is_off_the_list_until_it_is_asked_for(self):
        """
        The flag has to be reversible from the UI, which means the board must be
        able to SEE an archived team. `?archived=1` widens the queryset rather
        than replacing it, so the live teams come back too.
        """
        self.subject.is_archived = True
        self.subject.save(update_fields=["is_archived"])
        self.as_editor()

        default = self.client.get("/api/teams/")
        self.assertEqual(default.status_code, 200, default.content)
        names = [t["name"] for t in default.json()["results"]]
        self.assertNotIn("Field Desk", names)

        widened = self.client.get("/api/teams/?archived=1")
        widened_names = [t["name"] for t in widened.json()["results"]]
        self.assertIn("Field Desk", widened_names)
        self.assertIn("Coordination Desk", widened_names,
                      "asking for archived teams hid the live ones")

    def test_unarchiving_through_the_patch_brings_it_back(self):
        self.subject.is_archived = True
        self.subject.save(update_fields=["is_archived"])
        self.as_editor()
        resp = self.client.patch(f"/api/teams/{self.subject.id}/",
                                 {"is_archived": False}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.subject.refresh_from_db()
        self.assertFalse(self.subject.is_archived)

    # ── All-access, the dangerous one ───────────────────────────────────────

    def test_a_team_editor_cannot_grant_all_access(self):
        """
        THE ESCALATION. teams.update is "you may name and colour a team"; it is
        not "you may give a team every module". The field is read-only for this
        caller, so the value is dropped and the request still succeeds on the
        parts it was entitled to.
        """
        self.as_editor()
        resp = self.client.patch(
            f"/api/teams/{self.subject.id}/",
            {"name": "Field Desk", "is_all_access": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.subject.refresh_from_db()
        self.assertFalse(self.subject.is_all_access,
                         "teams.update alone granted a team every module")
        self.assertFalse(resp.json()["is_all_access"])

    def test_a_team_editor_cannot_take_all_access_away_either(self):
        """Read-only is read-only in both directions; revoking is a grid decision too."""
        self.subject.is_all_access = True
        self.subject.save(update_fields=["is_all_access"])
        self.as_editor()
        self.client.patch(f"/api/teams/{self.subject.id}/",
                          {"is_all_access": False}, format="json")
        self.subject.refresh_from_db()
        self.assertTrue(self.subject.is_all_access)

    def test_the_roles_right_may_grant_all_access_and_it_is_recorded(self):
        self.as_grid_keeper()
        resp = self.client.patch(f"/api/teams/{self.subject.id}/",
                                 {"is_all_access": True}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.subject.refresh_from_db()
        self.assertTrue(self.subject.is_all_access)
        self.assertTrue(
            TeamActivityLog.objects.filter(
                team=self.subject,
                action_type=TeamActivityLog.ActionType.PERMISSIONS_CHANGED,
            ).exists(),
            "an all-access grant left no activity entry",
        )


class LeadOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.editors = Team.objects.create(name="Coordination Desk")
        grant(cls.editors, "teams", view=True, update=True)
        cls.editor = User.objects.create_user(
            username="lead_editor", password="x", role="operations",
            email="lead_editor@iq-hub.com", team=cls.editors,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.editor)
        self.team = Team.objects.create(name="Field Desk")
        self.members = [
            User.objects.create_user(
                username=f"fd_{n}", password="x", role="operations",
                email=f"fd_{n}@iq-hub.com", team=self.team,
                first_name=f"Member{n}", last_name="Desk",
            )
            for n in range(1, 4)
        ]

    def set_leads(self, users):
        return self.client.post(
            f"/api/teams/{self.team.id}/assign-lead/",
            {"user_ids": [u.id for u in users]}, format="json",
        )

    def test_the_first_id_sent_becomes_the_primary_lead(self):
        third, first = self.members[2], self.members[0]
        resp = self.set_leads([third, first])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.team.refresh_from_db()
        self.assertEqual(self.team.team_lead_id, third.id)
        self.assertEqual([u["id"] for u in resp.json()["team_leads"]],
                         [third.id, first.id])

    def test_reordering_the_same_two_moves_the_primary(self):
        third, first = self.members[2], self.members[0]
        self.set_leads([third, first])
        resp = self.set_leads([first, third])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.team.refresh_from_db()
        self.assertEqual(self.team.team_lead_id, first.id)

    def test_every_member_named_is_marked_as_a_lead(self):
        self.set_leads(self.members[:2])
        flags = {u.username: User.objects.get(pk=u.pk).is_team_lead for u in self.members}
        self.assertEqual(flags, {"fd_1": True, "fd_2": True, "fd_3": False})

    def test_an_empty_list_clears_the_leads(self):
        self.set_leads(self.members[:2])
        resp = self.set_leads([])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.team.refresh_from_db()
        self.assertIsNone(self.team.team_lead_id)
        self.assertFalse(User.objects.filter(team=self.team, is_team_lead=True).exists())

    def test_someone_outside_the_team_cannot_be_made_its_lead(self):
        outsider = User.objects.create_user(
            username="fd_outsider", password="x", role="operations",
            email="fd_outsider@iq-hub.com", team=self.editors,
        )
        resp = self.set_leads([outsider])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.team.refresh_from_db()
        self.assertIsNone(self.team.team_lead_id)
        self.assertEqual(resp.json()["team_leads"], [])
