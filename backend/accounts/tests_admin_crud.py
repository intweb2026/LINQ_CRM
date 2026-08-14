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

from accounts.models import CRM_MODULES
from teams.models import Team, TeamActivityLog, TeamPermission

User = get_user_model()


class AdminCrudTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_role = Team.objects.create(
            name="crud_admin", is_all_access=True,
        )
        cls.admin = User.objects.create_user(
            username="crud_admin", password="x", role="admin",
            email="crud_admin@iq-hub.com",
        )
        cls.admin.team = cls.admin_role
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

    def test_an_admin_named_team_promotes_and_grants_django_rights(self):
        admin_team = Team.objects.create(name="Admin Team")
        # No `role` in the body, so the team's name is what decides. The form
        # sends one, but it sends the value this same rule already filled in.
        resp = self.client.post(
            "/api/users/",
            {"username": "promoted", "email": "promoted@iq-hub.com",
             "team_id": admin_team.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        subject = User.objects.get(username="promoted")
        self.assertEqual(subject.role, "admin")
        self.assertTrue(subject.is_superuser and subject.is_staff)

    def test_an_admin_is_not_demoted_by_being_moved_out_of_the_admin_team(self):
        """
        Deliberate, and older than this change. `role == ADMIN` short-circuits
        the whole team block, so admin is sticky in both directions: dragging
        someone out of the Admin team on the board does NOT take their rights.

        Pinned because it is the kind of asymmetry that reads as a bug from the
        board, and because the fix is not to make a drag revoke superuser
        silently; it is to change the role on the user form, which now works.
        """
        admin_team = Team.objects.create(name="Admin Team")
        sales = Team.objects.create(name="Sales Team")
        self.client.post(
            "/api/users/",
            {"username": "sticky_admin", "email": "sticky_admin@iq-hub.com",
             "team_id": admin_team.id},
            format="json",
        )
        subject = User.objects.get(username="sticky_admin")
        self.assertEqual(subject.role, "admin")

        self.client.post(
            "/api/teams/move-member/",
            {"user_id": subject.id, "destination_team_id": sales.id},
            format="json",
        )
        subject.refresh_from_db()
        self.assertEqual(subject.role, "admin", "a team move quietly revoked admin")
        self.assertTrue(subject.is_superuser)

        # The explicit path, which is what the Role field on the form drives.
        resp = self.client.patch(
            f"/api/users/{subject.id}/", {"role": "sales"}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        subject.refresh_from_db()
        self.assertEqual(subject.role, "sales")
        self.assertFalse(subject.is_superuser or subject.is_staff,
                         "demoting from admin left Django rights behind")

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

    # ── Permissions: the team grid, and one person's exceptions ─────────────

    def _grid(self, **flags):
        row = {"can_view": False, "can_create": False, "can_update": False, "can_delete": False}
        row.update(flags)
        return {"permissions": [dict(row, module=m) for m in CRM_MODULES]}

    def _delta(self, module, **flags):
        row = {"can_view": None, "can_create": None, "can_update": None, "can_delete": None}
        row.update(flags)
        return {"permissions": [dict(row, module=module)]}

    def test_a_team_grants_its_grid_to_every_member(self):
        team = Team.objects.create(name="Grid Holders")
        resp = self.client.put(
            f"/api/teams/{team.id}/permissions/",
            self._grid(can_view=True, can_create=True),
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        member = User.objects.create_user(
            username="member1", password="x", email="member1@iq-hub.com", team=team,
        )
        resolved = member.effective_permissions()
        for module in CRM_MODULES:
            self.assertTrue(resolved[module]["view"], module)
            self.assertTrue(resolved[module]["create"], module)
            self.assertFalse(resolved[module]["update"], module)

    def test_widening_a_team_widens_everyone_already_in_it(self):
        """
        THE POINT OF INHERITANCE. Members hold no copy of the grid, so a change
        to the team reaches them without touching a single user row.
        """
        team = Team.objects.create(name="Growing")
        member = User.objects.create_user(
            username="member2", password="x", email="member2@iq-hub.com", team=team,
        )
        self.assertFalse(member.effective_permissions()["events"]["view"])

        self.client.put(f"/api/teams/{team.id}/permissions/",
                        self._grid(can_view=True), format="json")

        member = User.objects.get(pk=member.pk)
        self.assertTrue(member.effective_permissions()["events"]["view"])

    def test_a_brand_new_account_can_be_given_exceptions_immediately(self):
        """
        The Add user form's two-request sequence, in order.

        The exceptions need an id to hang off, so the account has to exist first;
        the form does POST /users/ then PUT /users/{id}/permissions/ without an
        intervening reload. Pinned as a sequence because the failure it guards is
        an ordering one: computing the delta before the team has landed would
        measure it against the wrong grid, and the account would go live with
        access nobody asked for.
        """
        team = Team.objects.create(name="Fresh Start")
        self.client.put(f"/api/teams/{team.id}/permissions/",
                        self._grid(can_view=True), format="json")

        created = self._create_user(username="dayone", email="dayone@iq-hub.com",
                                    team_id=team.id).json()
        self.assertEqual(
            self.client.put(
                f"/api/users/{created['id']}/permissions/",
                self._delta("reports", can_create=True, can_delete=False),
                format="json",
            ).status_code, 200,
        )

        resolved = User.objects.get(pk=created["id"]).effective_permissions()
        self.assertTrue(resolved["reports"]["view"], "the team grant was lost")
        self.assertTrue(resolved["reports"]["create"], "the day-one grant did not apply")
        self.assertFalse(resolved["reports"]["delete"], "the day-one revoke did not apply")
        self.assertTrue(resolved["events"]["view"], "the exception leaked to another module")

    def test_a_user_can_be_granted_something_their_team_lacks(self):
        team = Team.objects.create(name="Narrow")
        self.client.put(f"/api/teams/{team.id}/permissions/",
                        self._grid(can_view=False), format="json")
        member = User.objects.create_user(
            username="extra", password="x", email="extra@iq-hub.com", team=team,
        )

        resp = self.client.put(
            f"/api/users/{member.id}/permissions/",
            self._delta("reports", can_view=True), format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        resolved = User.objects.get(pk=member.pk).effective_permissions()
        self.assertTrue(resolved["reports"]["view"], "the extra grant did not apply")
        self.assertFalse(resolved["events"]["view"], "the grant leaked to another module")

    def test_a_user_can_have_something_taken_away_that_their_team_grants(self):
        """The third state. A revoke has to beat the team, not merely not-add."""
        team = Team.objects.create(name="Wide")
        self.client.put(f"/api/teams/{team.id}/permissions/",
                        self._grid(can_view=True, can_delete=True), format="json")
        member = User.objects.create_user(
            username="norm", password="x", email="norm@iq-hub.com", team=team,
        )
        self.assertTrue(member.effective_permissions()["bookings"]["delete"])

        resp = self.client.put(
            f"/api/users/{member.id}/permissions/",
            self._delta("bookings", can_delete=False), format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        resolved = User.objects.get(pk=member.pk).effective_permissions()
        self.assertFalse(resolved["bookings"]["delete"], "the revoke was ignored")
        self.assertTrue(resolved["bookings"]["view"], "the revoke took the whole module")
        self.assertTrue(resolved["events"]["delete"], "the revoke leaked to another module")

    def test_an_untouched_cell_keeps_following_the_team(self):
        """
        null is INHERIT, not false. Storing the resolved value instead would
        freeze the person at the moment they were edited, and the next change to
        their team would pass them by.
        """
        team = Team.objects.create(name="Later")
        self.client.put(f"/api/teams/{team.id}/permissions/",
                        self._grid(can_view=True), format="json")
        member = User.objects.create_user(
            username="follower", password="x", email="follower@iq-hub.com", team=team,
        )
        self.client.put(f"/api/users/{member.id}/permissions/",
                        self._delta("reports", can_create=True), format="json")

        # The team now loses view on everything. The member kept an override on
        # reports.create only, so their view must follow the team down.
        self.client.put(f"/api/teams/{team.id}/permissions/",
                        self._grid(can_view=False), format="json")

        resolved = User.objects.get(pk=member.pk).effective_permissions()
        self.assertFalse(resolved["reports"]["view"], "an inherited cell did not follow the team")
        self.assertTrue(resolved["reports"]["create"], "the explicit grant was lost")

    def test_an_all_null_module_is_not_stored(self):
        team = Team.objects.create(name="Nulls")
        member = User.objects.create_user(
            username="nulls", password="x", email="nulls@iq-hub.com", team=team,
        )
        self.client.put(f"/api/users/{member.id}/permissions/",
                        self._delta("reports"), format="json")
        self.assertEqual(
            member.permission_overrides.count(), 0,
            "an override row with nothing in it was kept, and reads as an exception",
        )

    def test_setting_a_team_grid_twice_replaces_rather_than_duplicates(self):
        team = Team.objects.create(name="twice")
        self.client.put(f"/api/teams/{team.id}/permissions/", self._grid(can_view=True), format="json")
        self.client.put(f"/api/teams/{team.id}/permissions/", self._grid(can_view=False), format="json")
        self.assertEqual(team.permissions.count(), len(CRM_MODULES))
        self.assertFalse(any(p.can_view for p in team.permissions.all()),
                         "a revoked permission survived the second save")

    def test_an_unknown_module_is_refused_whole(self):
        team = Team.objects.create(name="unknown")
        resp = self.client.put(
            f"/api/teams/{team.id}/permissions/",
            {"permissions": [{"module": "not_a_module", "can_view": True}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(team.permissions.count(), 0)

    def test_an_all_access_team_opens_everything(self):
        team = Team.objects.create(name="Everything", is_all_access=True)
        member = User.objects.create_user(
            username="omni", password="x", email="omni@iq-hub.com", team=team,
        )
        resolved = member.effective_permissions()
        self.assertTrue(all(resolved[m][a] for m in CRM_MODULES
                            for a in ("view", "create", "update", "delete")))
        self.assertTrue(member.has_all_access)

    def test_a_user_with_no_team_has_nothing(self):
        loner = User.objects.create_user(
            username="loner", password="x", email="loner@iq-hub.com",
        )
        resolved = loner.effective_permissions()
        self.assertFalse(any(resolved[m][a] for m in CRM_MODULES
                             for a in ("view", "create", "update", "delete")))

    def test_deleting_a_team_takes_its_grid_with_it(self):
        team = Team.objects.create(name="doomed")
        self.client.put(f"/api/teams/{team.id}/permissions/", self._grid(can_view=True), format="json")
        team_id = team.id
        resp = self.client.delete(f"/api/teams/{team_id}/")
        self.assertEqual(resp.status_code, 204, resp.content)
        self.assertEqual(TeamPermission.objects.filter(team_id=team_id).count(), 0)

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
