"""
accounts/tests_team_manager.py
───────────────────────────────
Team Manager rights: one person, one team, and no way out of it.

THE SHAPE OF THE FEATURE
A super admin sets `User.managed_team`. That single column does two things and
nothing else:

  * it OPENS the Users module for that person, without their team's grid having
    to be touched (accounts/models.py, User.effective_permissions);
  * it PINS every write they make through /api/users/ to that one team
    (accounts/permissions.py, assert_can_manage_user / assert_can_place_in_team,
    reached from UserViewSet.get_object and UserWriteSerializer.validate).

WHAT THIS SUITE IS FOR
Each escape route out of the second half, checked one at a time — because every
one of them is a way to create or take over an account in a team that is not
yours, and a permission feature is only worth what its worst route allows:

    edit somebody in another team          UserViewSet.get_object
    delete somebody in another team        UserViewSet.get_object
    create an account into another team    UserWriteSerializer.validate
    create an account into NO team         UserWriteSerializer.validate
    move one of yours into another team    move_team + validate
    reset an administrator's password      assert_can_manage_user
    make somebody role=admin               UserWriteSerializer.validate
    make yourself manager of a second team UserWriteSerializer.validate
    rewrite a permission grid              set_permissions, gated on `roles`

The last three classes then check what must NOT have changed: a super admin is
untouched, and so is an ordinary account holding the users module through its
team's grid — that caller has no managed team, so none of the rules above apply
to it and it keeps the reach it had.

    python manage.py test accounts.tests_team_manager
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from teams.models import Team, TeamPermission

User = get_user_model()


def _user(username, team=None, role="sales", **extra):
    u = User.objects.create_user(
        username=username, password="x", role=role,
        email=f"{username}@iq-hub.com", **extra,
    )
    if team:
        u.team = team
        u.save()
    return u


class TeamManagerBaseTests(TestCase):
    """Shared cast: two teams, a super admin, a manager of one of them."""

    @classmethod
    def setUpTestData(cls):
        cls.sales = Team.objects.create(name="tm Sales")
        cls.dmd = Team.objects.create(name="tm DMD")

        # role=admin is NOT on its own enough to pass crm_permission(), which
        # reads the team's grid and knows nothing about the role column — see
        # accounts/tests_admin_crud.py, which builds its administrator the same
        # way. An all-access team is how a real super admin is configured here.
        cls.all_access = Team.objects.create(name="tm Everything", is_all_access=True)
        cls.superadmin = _user("tm_admin", team=cls.all_access, role="admin")
        cls.manager = _user("tm_manager", team=cls.sales)
        cls.manager.managed_team = cls.sales
        cls.manager.save()

        cls.sales_member = _user("tm_sales_member", team=cls.sales)
        cls.dmd_member = _user("tm_dmd_member", team=cls.dmd)

    def as_(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c


class ManagerRightsGrantTests(TeamManagerBaseTests):
    """The column opens the Users module, and only the Users module."""

    def test_manager_holds_every_users_action(self):
        perms = User.objects.get(pk=self.manager.pk).effective_permissions()
        self.assertEqual(
            {a: perms["users"][a] for a in ("view", "create", "update", "delete")},
            {"view": True, "create": True, "update": True, "delete": True},
        )

    def test_manager_can_read_teams_but_not_write_them(self):
        # The Users screen renders team names and its form offers a team, so the
        # read is load-bearing. The write is what would let them move members
        # between teams through the other door.
        perms = User.objects.get(pk=self.manager.pk).effective_permissions()
        self.assertTrue(perms["teams"]["view"])
        self.assertFalse(perms["teams"]["update"])

    def test_manager_is_not_a_super_admin(self):
        """
        The line between the two roles. `roles` is what governs permission grids;
        withholding it is the whole reason a manager cannot widen their own team.
        """
        from accounts.permissions import is_super_admin

        perms = User.objects.get(pk=self.manager.pk).effective_permissions()
        self.assertFalse(perms["roles"]["view"])
        self.assertFalse(perms["performance"]["view"])
        self.assertFalse(is_super_admin(self.manager))
        self.assertFalse(self.manager.has_all_access)

    def test_a_super_admin_can_revoke_a_named_action(self):
        """
        The grant is applied BEFORE per-user deltas, so it is still an ordinary
        default rather than an unremovable one. A manager who may not delete
        accounts is expressible.
        """
        from accounts.models import UserPermission

        UserPermission.objects.create(
            user=self.manager, module="users", can_delete=False,
        )
        perms = User.objects.get(pk=self.manager.pk).effective_permissions()
        self.assertTrue(perms["users"]["update"])
        self.assertFalse(perms["users"]["delete"])

    def test_my_permissions_reports_the_managed_team(self):
        r = self.as_(self.manager).get("/api/users/my-permissions/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["managed_team_id"], self.sales.id)
        self.assertEqual(r.data["managed_team_name"], "tm Sales")

    def test_my_permissions_reports_no_managed_team_for_a_super_admin(self):
        """A super admin is not restricted to one team, so the UI must not
        narrow their Users page to whatever the column happens to hold."""
        self.superadmin.managed_team = self.sales
        self.superadmin.save()
        r = self.as_(self.superadmin).get("/api/users/my-permissions/")
        self.assertIsNone(r.data["managed_team_id"])


class ManagerInsideOwnTeamTests(TeamManagerBaseTests):
    """What a manager IS allowed to do, so the restriction is not vacuous."""

    def test_edit_a_member_of_the_managed_team(self):
        r = self.as_(self.manager).patch(
            f"/api/users/{self.sales_member.id}/", {"first_name": "Ada"}, format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.sales_member.refresh_from_db()
        self.assertEqual(self.sales_member.first_name, "Ada")

    def test_create_lands_in_the_managed_team_without_naming_it(self):
        r = self.as_(self.manager).post(
            "/api/users/",
            {"username": "tm_new", "email": "tm_new@iq-hub.com"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(User.objects.get(username="tm_new").team_id, self.sales.id)

    def test_reset_a_members_password(self):
        r = self.as_(self.manager).patch(
            f"/api/users/{self.sales_member.id}/reset-password/",
            {"password": "longenough1", "confirm_password": "longenough1"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)

    def test_delete_a_member(self):
        r = self.as_(self.manager).delete(f"/api/users/{self.sales_member.id}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(User.objects.filter(pk=self.sales_member.pk).exists())


class ManagerOutsideOwnTeamTests(TeamManagerBaseTests):
    """Every route into another team, refused."""

    def test_cannot_edit_another_teams_member(self):
        r = self.as_(self.manager).patch(
            f"/api/users/{self.dmd_member.id}/", {"first_name": "Nope"}, format="json",
        )
        self.assertEqual(r.status_code, 403, r.data)
        self.dmd_member.refresh_from_db()
        self.assertEqual(self.dmd_member.first_name, "")

    def test_cannot_delete_another_teams_member(self):
        r = self.as_(self.manager).delete(f"/api/users/{self.dmd_member.id}/")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.dmd_member.pk).exists())

    def test_cannot_reset_another_teams_password(self):
        r = self.as_(self.manager).patch(
            f"/api/users/{self.dmd_member.id}/reset-password/",
            {"password": "longenough1", "confirm_password": "longenough1"},
            format="json",
        )
        self.assertEqual(r.status_code, 403)

    def test_cannot_toggle_another_teams_status(self):
        r = self.as_(self.manager).patch(
            f"/api/users/{self.dmd_member.id}/toggle-status/", {}, format="json",
        )
        self.assertEqual(r.status_code, 403)
        self.dmd_member.refresh_from_db()
        self.assertEqual(self.dmd_member.status, "active")

    def test_cannot_create_into_another_team(self):
        r = self.as_(self.manager).post(
            "/api/users/",
            {"username": "tm_x", "email": "tm_x@iq-hub.com", "team_id": self.dmd.id},
            format="json",
        )
        self.assertEqual(r.status_code, 403, r.data)
        self.assertFalse(User.objects.filter(username="tm_x").exists())

    def test_cannot_create_an_unassigned_account(self):
        """An account in no team is nobody's, which is another way of saying it
        is not the manager's to make."""
        r = self.as_(self.manager).post(
            "/api/users/",
            {"username": "tm_y", "email": "tm_y@iq-hub.com", "team_id": None},
            format="json",
        )
        self.assertEqual(r.status_code, 403, r.data)

    def test_cannot_move_a_member_out_via_patch(self):
        r = self.as_(self.manager).patch(
            f"/api/users/{self.sales_member.id}/",
            {"team_id": self.dmd.id}, format="json",
        )
        self.assertEqual(r.status_code, 403, r.data)
        self.sales_member.refresh_from_db()
        self.assertEqual(self.sales_member.team_id, self.sales.id)

    def test_cannot_move_a_member_out_via_move_team(self):
        """The dedicated endpoint is a second door onto the same change, so it
        gets the same check — on the DESTINATION, which get_object cannot see."""
        r = self.as_(self.manager).patch(
            f"/api/users/{self.sales_member.id}/move-team/",
            {"team_id": self.dmd.id}, format="json",
        )
        self.assertEqual(r.status_code, 403, r.data)
        self.sales_member.refresh_from_db()
        self.assertEqual(self.sales_member.team_id, self.sales.id)

    def test_cannot_unassign_a_member_via_move_team(self):
        r = self.as_(self.manager).patch(
            f"/api/users/{self.sales_member.id}/move-team/", {}, format="json",
        )
        self.assertEqual(r.status_code, 403, r.data)
        self.sales_member.refresh_from_db()
        self.assertEqual(self.sales_member.team_id, self.sales.id)

    def test_cannot_pull_another_teams_member_in(self):
        """Naming your own team as the destination does not make somebody else's
        account yours to move — get_object refuses before the team is read."""
        r = self.as_(self.manager).patch(
            f"/api/users/{self.dmd_member.id}/",
            {"team_id": self.sales.id}, format="json",
        )
        self.assertEqual(r.status_code, 403, r.data)


class ManagerEscalationTests(TeamManagerBaseTests):
    """Routes that would turn a manager into a super admin."""

    def test_cannot_mint_an_administrator(self):
        """
        `role` is a label that grants nothing — except this value, which
        User.save() turns into is_superuser and is_staff.
        """
        r = self.as_(self.manager).post(
            "/api/users/",
            {"username": "tm_root", "email": "tm_root@iq-hub.com", "role": "admin"},
            format="json",
        )
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("role", r.data)

    def test_the_role_follows_the_team_on_create(self):
        """
        A manager of Sales cannot file somebody as Operations. The team's NAME
        decides, through role_from_team_name, so a named role is dropped and
        User.save() derives the same answer it would have anyway.
        """
        r = self.as_(self.manager).post(
            "/api/users/",
            {"username": "tm_role", "email": "tm_role@iq-hub.com",
             "role": "operations"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(User.objects.get(username="tm_role").role, "sales")

    def test_the_role_follows_a_differently_named_team(self):
        """The derivation is the team's, not a hardcoded 'sales' — a manager of
        DMD produces Data Mining accounts."""
        self.manager.managed_team = self.dmd
        self.manager.save()
        r = self.as_(User.objects.get(pk=self.manager.pk)).post(
            "/api/users/",
            {"username": "tm_dmd_new", "email": "tm_dmd_new@iq-hub.com",
             "role": "speaker_sales"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(User.objects.get(username="tm_dmd_new").role, "data_mining")

    def test_cannot_change_an_existing_members_role(self):
        """
        On an edit that does not move teams nothing re-derives, so the stored
        role stays exactly as the super admin who set it left it.
        """
        self.sales_member.role = "operations"
        self.sales_member.save()
        r = self.as_(self.manager).patch(
            f"/api/users/{self.sales_member.id}/",
            {"role": "telemarketing", "first_name": "Ada"}, format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.sales_member.refresh_from_db()
        self.assertEqual(self.sales_member.role, "operations")
        # The rest of the edit still lands — the role is ignored, not fatal.
        self.assertEqual(self.sales_member.first_name, "Ada")

    def test_a_super_admin_still_names_roles_by_hand(self):
        """The lock is the manager's, not everyone's: picking a role that the
        team's name would not imply is an existing, deliberate feature."""
        r = self.as_(self.superadmin).patch(
            f"/api/users/{self.sales_member.id}/",
            {"role": "operations"}, format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.sales_member.refresh_from_db()
        self.assertEqual(self.sales_member.role, "operations")

    def test_cannot_promote_a_member_to_administrator(self):
        r = self.as_(self.manager).patch(
            f"/api/users/{self.sales_member.id}/", {"role": "admin"}, format="json",
        )
        self.assertEqual(r.status_code, 400, r.data)
        self.sales_member.refresh_from_db()
        self.assertFalse(self.sales_member.is_superuser)

    def test_cannot_grant_themselves_a_second_team(self):
        r = self.as_(self.manager).patch(
            f"/api/users/{self.manager.id}/",
            {"managed_team_id": self.dmd.id}, format="json",
        )
        self.assertEqual(r.status_code, 400, r.data)
        self.manager.refresh_from_db()
        self.assertEqual(self.manager.managed_team_id, self.sales.id)

    def test_cannot_appoint_another_manager(self):
        r = self.as_(self.manager).patch(
            f"/api/users/{self.sales_member.id}/",
            {"managed_team_id": self.sales.id}, format="json",
        )
        self.assertEqual(r.status_code, 400, r.data)
        self.sales_member.refresh_from_db()
        self.assertIsNone(self.sales_member.managed_team_id)

    def test_cannot_touch_an_administrator_sitting_in_their_team(self):
        """
        Otherwise the restriction lasts two clicks: reset the password of an
        admin who happens to be in your team, then sign in as them.
        """
        admin_in_team = _user("tm_admin_in_sales", team=self.sales, role="admin")
        r = self.as_(self.manager).patch(
            f"/api/users/{admin_in_team.id}/reset-password/",
            {"password": "longenough1", "confirm_password": "longenough1"},
            format="json",
        )
        self.assertEqual(r.status_code, 403, r.data)

    def test_cannot_rewrite_a_permission_grid(self):
        """Deciding what somebody MAY DO answers to `roles`, which no manager
        holds by being one."""
        r = self.as_(self.manager).put(
            f"/api/users/{self.sales_member.id}/permissions/",
            {"permissions": [{"module": "users", "can_view": True}]},
            format="json",
        )
        self.assertEqual(r.status_code, 403, r.data)

    def test_cannot_re_role_the_company_with_sync_roles(self):
        """
        /api/users/sync-roles/ rewrites the `role` column of EVERY user in one
        POST, across every team, and User.save() turns role=admin into
        is_superuser. It was open to any authenticated session, which made it a
        direct-API route straight around this whole feature.
        """
        r = self.as_(self.manager).post("/api/users/sync-roles/", {}, format="json")
        self.assertEqual(r.status_code, 403, r.data)

    def test_cannot_widen_their_own_team(self):
        r = self.as_(self.manager).put(
            f"/api/teams/{self.sales.id}/permissions/",
            {"permissions": [], "is_all_access": True},
            format="json",
        )
        self.assertEqual(r.status_code, 403, r.data)
        self.sales.refresh_from_db()
        self.assertFalse(self.sales.is_all_access)


class SuperAdminAssignmentTests(TeamManagerBaseTests):
    """Granting, reassigning and removing the rights."""

    def test_grant_makes_a_manager(self):
        target = _user("tm_promote", team=self.dmd)
        self.assertFalse(target.effective_permissions()["users"]["view"])

        r = self.as_(self.superadmin).patch(
            f"/api/users/{target.id}/", {"managed_team_id": self.dmd.id}, format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(
            User.objects.get(pk=target.pk).effective_permissions()["users"]["create"]
        )

    def test_reassignment_moves_the_reach(self):
        r = self.as_(self.superadmin).patch(
            f"/api/users/{self.manager.id}/",
            {"managed_team_id": self.dmd.id}, format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)

        client = self.as_(User.objects.get(pk=self.manager.pk))
        # The team they used to run is now somebody else's.
        self.assertEqual(
            client.patch(f"/api/users/{self.sales_member.id}/",
                         {"first_name": "No"}, format="json").status_code, 403,
        )
        # And the one they were given is theirs.
        self.assertEqual(
            client.patch(f"/api/users/{self.dmd_member.id}/",
                         {"first_name": "Yes"}, format="json").status_code, 200,
        )

    def test_removal_takes_the_module_away(self):
        r = self.as_(self.superadmin).patch(
            f"/api/users/{self.manager.id}/", {"managed_team_id": None}, format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)

        demoted = User.objects.get(pk=self.manager.pk)
        self.assertIsNone(demoted.managed_team_id)
        self.assertFalse(demoted.effective_permissions()["users"]["view"])
        self.assertEqual(
            self.as_(demoted).patch(f"/api/users/{self.sales_member.id}/",
                                    {"first_name": "No"}, format="json").status_code,
            403,
        )

    def test_the_managed_team_need_not_be_the_users_own_team(self):
        """A head of department can sit in one team and run another."""
        outsider = _user("tm_outsider", team=self.dmd)
        self.as_(self.superadmin).patch(
            f"/api/users/{outsider.id}/", {"managed_team_id": self.sales.id},
            format="json",
        )
        self.assertEqual(
            self.as_(User.objects.get(pk=outsider.pk)).patch(
                f"/api/users/{self.sales_member.id}/", {"first_name": "Ok"},
                format="json",
            ).status_code, 200,
        )

    def test_deleting_the_team_removes_the_rights(self):
        """SET_NULL, so a manager is never left pointing at a team that is gone
        — which would be a reach nobody can see or revoke."""
        self.sales_member.delete()
        self.manager.team = None
        self.manager.save()
        self.sales.delete()
        self.manager.refresh_from_db()
        self.assertIsNone(self.manager.managed_team_id)
        self.assertFalse(self.manager.effective_permissions()["users"]["view"])


class UnchangedForEveryoneElseTests(TeamManagerBaseTests):
    """The feature must be invisible to callers who are not managers."""

    def test_super_admin_still_reaches_every_team(self):
        client = self.as_(self.superadmin)
        self.assertEqual(
            client.patch(f"/api/users/{self.dmd_member.id}/",
                         {"first_name": "Fine"}, format="json").status_code, 200,
        )
        self.assertEqual(
            client.post("/api/users/",
                        {"username": "tm_sa", "email": "tm_sa@iq-hub.com",
                         "team_id": self.dmd.id}, format="json").status_code, 201,
        )

    def test_a_grid_granted_account_keeps_its_old_reach(self):
        """
        Someone holding the users module through their TEAM'S grid, with no
        managed team, is governed by that grid alone exactly as before. Narrowing
        them would be this feature quietly re-scoping accounts nobody named.
        """
        for module, cells in (("users", dict(can_view=True, can_create=True,
                                             can_update=True)),):
            TeamPermission.objects.create(team=self.dmd, module=module, **cells)
        staffer = _user("tm_hr", team=self.dmd)

        client = self.as_(User.objects.get(pk=staffer.pk))
        self.assertEqual(
            client.patch(f"/api/users/{self.sales_member.id}/",
                         {"first_name": "Across"}, format="json").status_code, 200,
        )
        self.assertEqual(
            client.post("/api/users/",
                        {"username": "tm_hr_new", "email": "tm_hr_new@iq-hub.com",
                         "team_id": self.sales.id}, format="json").status_code, 201,
        )

    def test_an_ordinary_account_still_cannot_write_users(self):
        plain = _user("tm_plain", team=self.sales)
        self.assertEqual(
            self.as_(plain).patch(f"/api/users/{self.sales_member.id}/",
                                  {"first_name": "No"}, format="json").status_code, 403,
        )

    def test_the_directory_stays_readable(self):
        """
        /api/users/ is the app-wide list behind every owner and assignee
        dropdown, readable by any authenticated session. A manager must not see
        LESS of it than the people they manage — the restriction is on writing.
        """
        listed = self.as_(self.manager).get("/api/users/")
        self.assertEqual(listed.status_code, 200)
        names = {row["username"] for row in listed.data["results"]}
        self.assertIn("tm_dmd_member", names)
