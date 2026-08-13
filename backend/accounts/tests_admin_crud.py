"""
accounts/tests_admin_crud.py
─────────────────────────────
The three admin surfaces the UI drives: adding and editing a user, a role and a
team.

Every bug covered here answered with a SUCCESS or a plausible-looking failure,
which is why none of them showed up in the existing suite:

  * toggle-status refused an empty body, so the Deactivate button — whose whole
    job is to flip a status — answered 400 on every click.
  * A write returned the write serializer's own fields, which carry no `id`, so
    the client had nothing to key the created row on.
  * Two accounts could share an email. Sign-in is BY email, so the OTP endpoint's
    `User.objects.get(email__iexact=...)` then raised MultipleObjectsReturned and
    both accounts answered 500 — for a collision created weeks earlier.
  * Two teams could not share a name: `slug` is unique and was taken verbatim
    from slugify(name), so the second one raised IntegrityError out of create.

Companion to tests_wire_probe.py, which asserts what the FRONTEND sends. This
one asserts what the backend does with it.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import CRM_MODULES, CustomRole
from teams.models import Team, TeamActivityLog

User = get_user_model()


class AdminCrudTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_role = CustomRole.objects.create(
            name="crud_admin", display_label="CRUD Admin", is_all_access=True,
        )
        cls.admin = User.objects.create_user(
            username="crud_admin", password="x", role="admin",
            email="crud_admin@iq-hub.com",
        )
        cls.admin.custom_role = cls.admin_role
        cls.admin.save()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    # ── Users ───────────────────────────────────────────────────────────────

    def _create_user(self, **over):
        body = {
            "username": "ada", "email": "ada@iq-hub.com",
            "first_name": "Ada", "last_name": "Lovelace",
            "role": "sales", "status": "active",
        }
        body.update(over)
        return self.client.post("/api/users/", body, format="json")

    def test_creating_a_user_answers_with_the_read_shape(self):
        resp = self._create_user()
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()
        # The client keys its row on `id` and renders `full_name`. The write
        # serializer declares neither, so echoing it back left both undefined.
        for field in ("id", "full_name", "team_id", "status", "assigned_events"):
            self.assertIn(field, data, f"write response is missing {field}: {sorted(data)}")
        self.assertEqual(data["full_name"], "Ada Lovelace")

    def test_a_second_account_cannot_take_an_existing_email(self):
        self.assertEqual(self._create_user().status_code, 201)
        resp = self._create_user(username="ada2")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("email", resp.json())

    def test_email_uniqueness_ignores_case(self):
        self.assertEqual(self._create_user().status_code, 201)
        resp = self._create_user(username="ada2", email="ADA@IQ-HUB.COM")
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_a_user_cannot_be_created_without_an_email(self):
        """Sign-in is by email; an account without one cannot be used at all."""
        resp = self.client.post(
            "/api/users/",
            {"username": "noemail", "role": "sales", "status": "active"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("email", resp.json())

    def test_editing_a_user_may_keep_its_own_email(self):
        """The uniqueness check must exclude the row being edited."""
        created = self._create_user().json()
        resp = self.client.patch(
            f"/api/users/{created['id']}/",
            {"email": "ada@iq-hub.com", "first_name": "Augusta"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(User.objects.get(pk=created["id"]).first_name, "Augusta")

    def test_editing_a_user_without_a_password_leaves_it_alone(self):
        created = self._create_user(password="hunter2hunter2").json()
        resp = self.client.patch(
            f"/api/users/{created['id']}/", {"first_name": "Augusta"}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(User.objects.get(pk=created["id"]).check_password("hunter2hunter2"))

    def test_a_blank_password_does_not_overwrite_the_stored_one(self):
        """The edit form always sends its password box; untouched means blank."""
        created = self._create_user(password="hunter2hunter2").json()
        resp = self.client.patch(
            f"/api/users/{created['id']}/", {"password": ""}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(User.objects.get(pk=created["id"]).check_password("hunter2hunter2"))

    def test_toggle_status_with_no_body_flips_both_ways(self):
        subject = User.objects.create_user(
            username="flip", password="x", email="flip@iq-hub.com",
        )
        url = f"/api/users/{subject.id}/toggle-status/"

        self.assertEqual(self.client.patch(url, {}, format="json").status_code, 200)
        subject.refresh_from_db()
        self.assertEqual(subject.status, "inactive")
        self.assertFalse(subject.is_active)

        self.assertEqual(self.client.patch(url, {}, format="json").status_code, 200)
        subject.refresh_from_db()
        self.assertEqual(subject.status, "active")
        self.assertTrue(subject.is_active)

    def test_toggle_status_still_accepts_an_explicit_status(self):
        subject = User.objects.create_user(
            username="susp", password="x", email="susp@iq-hub.com",
        )
        resp = self.client.patch(
            f"/api/users/{subject.id}/toggle-status/", {"status": "suspended"}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        subject.refresh_from_db()
        self.assertEqual(subject.status, "suspended")

    def test_toggle_status_rejects_a_status_that_is_not_one(self):
        subject = User.objects.create_user(
            username="bogus", password="x", email="bogus@iq-hub.com",
        )
        resp = self.client.patch(
            f"/api/users/{subject.id}/toggle-status/", {"status": "banana"}, format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_you_cannot_deactivate_yourself(self):
        resp = self.client.patch(
            f"/api/users/{self.admin.id}/toggle-status/", {}, format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_reset_password_sets_it(self):
        subject = User.objects.create_user(
            username="pw", password="x", email="pw@iq-hub.com",
        )
        resp = self.client.patch(
            f"/api/users/{subject.id}/reset-password/",
            {"password": "hunter2hunter2", "confirm_password": "hunter2hunter2"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        subject.refresh_from_db()
        self.assertTrue(subject.check_password("hunter2hunter2"))

    def test_reset_password_refuses_a_short_one(self):
        """The create/edit form enforces 8; this path must agree with it."""
        subject = User.objects.create_user(
            username="pw2", password="x", email="pw2@iq-hub.com",
        )
        resp = self.client.patch(
            f"/api/users/{subject.id}/reset-password/",
            {"password": "short", "confirm_password": "short"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        subject.refresh_from_db()
        self.assertFalse(subject.check_password("short"))

    # ── Role vs team, the field the form lets you override ──────────────────

    def test_a_team_sets_the_role_when_the_request_does_not(self):
        """Placing someone in a team with no opinion of their own still derives."""
        team = Team.objects.create(name="Market Research")
        resp = self.client.post(
            "/api/users/",
            {"username": "derived", "email": "derived@iq-hub.com", "team_id": team.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(User.objects.get(username="derived").role, "market_research")

    def test_an_explicit_role_beats_the_one_the_team_implies(self):
        """
        The Role field on the form has to mean something. Picking Operations for
        someone going into "Sales Team" used to be overwritten with Sales inside
        save(), before the response was even built.
        """
        team = Team.objects.create(name="Sales Team")
        resp = self._create_user(username="override", email="override@iq-hub.com",
                                 team_id=team.id, role="operations")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(User.objects.get(username="override").role, "operations")

    def test_an_explicit_role_survives_a_later_unrelated_save(self):
        """
        THE REASON "editable" NEEDED MORE THAN A FRONTEND CHANGE.

        save() re-derived the role every single time, so an override lasted until
        the next write of any kind. Deactivating the account was enough to undo
        it, and nothing in that request mentioned roles.
        """
        team = Team.objects.create(name="Sales Team")
        self._create_user(username="sticky", email="sticky@iq-hub.com",
                          team_id=team.id, role="operations")
        subject = User.objects.get(username="sticky")

        resp = self.client.patch(
            f"/api/users/{subject.id}/toggle-status/", {}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        subject.refresh_from_db()
        self.assertEqual(subject.role, "operations", "a status toggle re-rolled the user")

        self.client.patch(
            f"/api/users/{subject.id}/reset-password/",
            {"password": "hunter2hunter2", "confirm_password": "hunter2hunter2"},
            format="json",
        )
        subject.refresh_from_db()
        self.assertEqual(subject.role, "operations", "a password reset re-rolled the user")

    def test_moving_someone_to_another_team_still_re_derives(self):
        """The override is not a permanent lock; a new placement is a new answer."""
        sales = Team.objects.create(name="Sales Team")
        mr = Team.objects.create(name="Market Research")
        self._create_user(username="mover", email="mover@iq-hub.com",
                          team_id=sales.id, role="operations")
        subject = User.objects.get(username="mover")

        resp = self.client.post(
            "/api/teams/move-member/",
            {"user_id": subject.id, "destination_team_id": mr.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        subject.refresh_from_db()
        self.assertEqual(subject.role, "market_research")

    def test_an_admin_team_promotes_and_leaving_it_demotes(self):
        admin_team = Team.objects.create(name="Admin Team")
        sales = Team.objects.create(name="Sales Team")

        self._create_user(username="promoted", email="promoted@iq-hub.com",
                          team_id=admin_team.id)
        subject = User.objects.get(username="promoted")
        self.assertEqual(subject.role, "admin")
        self.assertTrue(subject.is_superuser and subject.is_staff)

        self.client.post(
            "/api/teams/move-member/",
            {"user_id": subject.id, "destination_team_id": sales.id},
            format="json",
        )
        subject.refresh_from_db()
        self.assertEqual(subject.role, "sales")
        self.assertFalse(subject.is_superuser or subject.is_staff)

    def test_an_explicit_non_admin_role_does_not_pick_up_superuser(self):
        """
        The team branch used to grant superuser off the NAME alone. With the role
        no longer being overwritten, that would have handed staff and superuser
        rights to someone deliberately saved as Sales.
        """
        team = Team.objects.create(name="Admin Support")
        self._create_user(username="notadmin", email="notadmin@iq-hub.com",
                          team_id=team.id, role="sales")
        subject = User.objects.get(username="notadmin")
        self.assertEqual(subject.role, "sales")
        self.assertFalse(subject.is_superuser, "an explicit Sales user became a superuser")
        self.assertFalse(subject.is_staff)

    def test_sync_roles_still_re_derives_everyone(self):
        """
        The escape hatch after a RENAME. save() no longer touches the role of a
        user whose team did not change, so this is the only thing that brings
        existing members back in line with a team's new name.
        """
        team = Team.objects.create(name="Sales Team")
        self._create_user(username="renamed", email="renamed@iq-hub.com", team_id=team.id)
        team.name = "Market Research"
        team.save()

        resp = self.client.post("/api/users/sync-roles/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(User.objects.get(username="renamed").role, "market_research")

    # ── Roles ───────────────────────────────────────────────────────────────

    def _grid(self, **flags):
        row = {"can_view": False, "can_create": False, "can_update": False, "can_delete": False}
        row.update(flags)
        return {"permissions": [dict(row, module=m) for m in CRM_MODULES]}

    def test_creating_a_role_and_granting_it_permissions(self):
        resp = self.client.post(
            "/api/roles/",
            {"name": "regional_manager", "display_label": "Regional Manager",
             "color": "#009CBC", "description": "Runs a region"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        role_id = resp.json()["id"]

        resp = self.client.put(
            f"/api/roles/{role_id}/permissions/",
            self._grid(can_view=True, can_create=True),
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        role = CustomRole.objects.get(pk=role_id)
        self.assertEqual(role.permissions.count(), len(CRM_MODULES))
        for perm in role.permissions.all():
            self.assertTrue(perm.can_view)
            self.assertTrue(perm.can_create)
            self.assertFalse(perm.can_update)
            self.assertFalse(perm.can_delete)

    def test_setting_permissions_twice_replaces_rather_than_duplicates(self):
        role = CustomRole.objects.create(name="twice", display_label="Twice")
        self.client.put(f"/api/roles/{role.id}/permissions/", self._grid(can_view=True), format="json")
        self.client.put(f"/api/roles/{role.id}/permissions/", self._grid(can_view=False), format="json")
        self.assertEqual(role.permissions.count(), len(CRM_MODULES))
        self.assertFalse(any(p.can_view for p in role.permissions.all()),
                         "a revoked permission survived the second save")

    def test_a_role_name_is_unique(self):
        CustomRole.objects.create(name="dup", display_label="Dup")
        resp = self.client.post(
            "/api/roles/", {"name": "dup", "display_label": "Dup Again"}, format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_editing_a_role_keeps_its_name_and_permissions(self):
        role = CustomRole.objects.create(name="keep", display_label="Keep")
        self.client.put(f"/api/roles/{role.id}/permissions/", self._grid(can_view=True), format="json")
        resp = self.client.patch(
            f"/api/roles/{role.id}/", {"display_label": "Kept", "color": "#111111"}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        role.refresh_from_db()
        self.assertEqual(role.name, "keep", "the permission key changed under the label")
        self.assertEqual(role.display_label, "Kept")
        self.assertTrue(all(p.can_view for p in role.permissions.all()))

    def test_an_unknown_module_is_refused_whole(self):
        role = CustomRole.objects.create(name="unknown", display_label="Unknown")
        resp = self.client.put(
            f"/api/roles/{role.id}/permissions/",
            {"permissions": [{"module": "not_a_module", "can_view": True}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(role.permissions.count(), 0)

    def test_deleting_a_role_leaves_its_holders_without_one(self):
        role = CustomRole.objects.create(name="doomed", display_label="Doomed")
        holder = User.objects.create_user(
            username="holder", password="x", email="holder@iq-hub.com",
        )
        holder.custom_role = role
        holder.save()

        resp = self.client.delete(f"/api/roles/{role.id}/")
        self.assertEqual(resp.status_code, 204, resp.content)
        holder.refresh_from_db()
        self.assertIsNone(holder.custom_role, "the holder was deleted along with the role")

    # ── Teams ───────────────────────────────────────────────────────────────

    def test_creating_a_team(self):
        resp = self.client.post(
            "/api/teams/",
            {"name": "Market Research", "color": "#009CBC", "description": "MR"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        team = Team.objects.get(name="Market Research")
        self.assertEqual(team.slug, "market-research")
        self.assertTrue(
            TeamActivityLog.objects.filter(
                team=team, action_type=TeamActivityLog.ActionType.TEAM_CREATED
            ).exists()
        )

    def test_two_teams_may_share_a_name(self):
        """
        `slug` is unique and was derived verbatim, so the second create raised
        IntegrityError — a 500 whose body named neither the field nor the clash.
        """
        first = self.client.post("/api/teams/", {"name": "Sales"}, format="json")
        second = self.client.post("/api/teams/", {"name": "Sales"}, format="json")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 201, second.content)
        slugs = list(Team.objects.filter(name="Sales").values_list("slug", flat=True))
        self.assertEqual(len(set(slugs)), 2, slugs)

    def test_renaming_a_team_is_logged(self):
        team = Team.objects.create(name="Old Name")
        resp = self.client.patch(f"/api/teams/{team.id}/", {"name": "New Name"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        team.refresh_from_db()
        self.assertEqual(team.name, "New Name")
        self.assertTrue(
            TeamActivityLog.objects.filter(
                team=team, action_type=TeamActivityLog.ActionType.TEAM_RENAMED
            ).exists()
        )

    def test_editing_a_team_without_renaming_it_logs_nothing(self):
        team = Team.objects.create(name="Steady")
        resp = self.client.patch(f"/api/teams/{team.id}/", {"color": "#123456"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(
            TeamActivityLog.objects.filter(
                team=team, action_type=TeamActivityLog.ActionType.TEAM_RENAMED
            ).exists()
        )

    def test_a_team_with_members_cannot_be_deleted(self):
        team = Team.objects.create(name="Populated")
        User.objects.create_user(
            username="member", password="x", email="member@iq-hub.com", team=team,
        )
        resp = self.client.delete(f"/api/teams/{team.id}/")
        self.assertEqual(resp.status_code, 409, resp.content)
        # The reason has to be readable — the client shows `detail` verbatim.
        self.assertIn("member", resp.json()["detail"].lower())
        self.assertTrue(Team.objects.filter(pk=team.id).exists())

    def test_an_empty_team_can_be_deleted(self):
        team = Team.objects.create(name="Empty")
        resp = self.client.delete(f"/api/teams/{team.id}/")
        self.assertEqual(resp.status_code, 204, resp.content)
        self.assertFalse(Team.objects.filter(pk=team.id).exists())

    def test_archiving_a_team_hides_it_from_the_board(self):
        team = Team.objects.create(name="Archivable")
        resp = self.client.post(f"/api/teams/{team.id}/archive/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["is_archived"])

        listed = self.client.get("/api/teams/").json()
        rows = listed["results"] if isinstance(listed, dict) else listed
        self.assertNotIn(team.id, [t["id"] for t in rows])
