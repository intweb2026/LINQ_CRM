"""
ticket_central/tests_seed_dmd_assignees.py
───────────────────────────────────────────
The seeded DMD accounts must land in the Data Mining team.

THE FAILURE THIS PINS
The command first created them with team=None, which looks harmless and is not.
Almost everything on the user record is scoped BY TEAM, and the Reporting manager
picker (frontend lib/reporting.js managerOptionGroups) offers exactly three
groups: the chosen team's leads, that team's appointed manager — `managed_team`,
set by "Manager of" on the user form — and the administrators. A teamless account
matches neither of the first two, so its Reporting manager field fell all the way
through to the administrator list and the person appointed to run Data Mining was
never offered for any of the 92 accounts.

The repair is asserted with NO TICKETS AT ALL, deliberately. The names the
command creates from come out of the tickets table, and the repair used to live
inside that loop — so on an emptied table, a fresh environment, or a run with a
higher --min-count, it silently did nothing in precisely the case somebody
re-runs it to fix things.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from teams.models import Team
from ticket_central.models import Ticket

User = get_user_model()


class SeedDmdAssigneesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Named so role_from_team_name derives data_mining from it, which is how
        # the command finds it — not by id and not by a hardcoded "DMD Team".
        cls.team = Team.objects.create(name="DMD Team", is_all_access=False)

    def seed(self, **kwargs):
        call_command("seed_dmd_assignees", verbosity=0, **kwargs)

    def test_created_accounts_land_in_the_data_mining_team(self):
        for _ in range(20):
            Ticket.objects.create(purpose="PIK", assign_name="Vanshika Parmar")
        self.seed()
        user = User.objects.get(first_name="Vanshika", last_name="Parmar")
        self.assertEqual(user.team_id, self.team.id)
        self.assertEqual(user.role, User.Role.DATA_MINING)
        # Active so it lists; unable to sign in until somebody sets it up.
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertFalse(user.login_access)
        self.assertFalse(user.has_usable_password())

    def test_a_stranded_account_is_repaired_with_no_tickets_at_all(self):
        stranded = User.objects.create_user(
            username="sc.mahek.soni", email="sc.mahek.soni@dmd.invalid",
            first_name="SC - Mahek", last_name="Soni",
            role=User.Role.DATA_MINING, team=None,
        )
        self.assertEqual(Ticket.objects.count(), 0)
        self.seed()
        stranded.refresh_from_db()
        self.assertEqual(stranded.team_id, self.team.id)

    def test_a_real_user_is_never_moved(self):
        """
        The repair is narrowed to the placeholder domain. A real person who
        happens to share a name with a stored assignee must not be pulled out of
        the team somebody deliberately put them in — or left teamless if that is
        what was intended.
        """
        other = Team.objects.create(name="Sales Team", is_all_access=False)
        placed = User.objects.create_user(
            username="real.person", email="real.person@iq-hub.com",
            first_name="Real", last_name="Person", team=other,
        )
        teamless = User.objects.create_user(
            username="teamless", email="teamless@iq-hub.com",
            first_name="Team", last_name="Less", team=None,
        )
        self.seed()
        placed.refresh_from_db()
        teamless.refresh_from_db()
        self.assertEqual(placed.team_id, other.id)
        self.assertIsNone(teamless.team_id)

    def test_dry_run_writes_nothing(self):
        User.objects.create_user(
            username="dry.stranded", email="dry.stranded@dmd.invalid",
            first_name="Dry", last_name="Stranded",
            role=User.Role.DATA_MINING, team=None,
        )
        self.seed(dry_run=True)
        self.assertIsNone(User.objects.get(username="dry.stranded").team_id)
