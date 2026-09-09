"""
ticket_central/tests_normalize_dmd_accounts.py
───────────────────────────────────────────────
The accounts half of normalize_dmd_assignees.

THE FAILURE THIS PINS
seed_dmd_assignees builds accounts from the stored assignee spellings verbatim,
prefix and all. Seed a table that has not been normalised yet and one person
ends up holding two accounts, "SC - Mahek Soni" beside the real "Mahek Soni",
and the mass-update dropdown offers them both. Normalising the ticket columns
afterwards does not clean that up, because ACCOUNT_RENAMES is a hand-written
table of three that never mentioned them.

Kept apart from tests_normalize_dmd_assignees.py, which covers the ticket
columns, only because that file is being edited in parallel.
"""
import io

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from ticket_central.management.commands.normalize_dmd_assignees import (
    account_plan,
)

User = get_user_model()


def make_user(username, first, last, **kwargs):
    return User.objects.create(
        username=username, first_name=first, last_name=last,
        role=kwargs.pop("role", User.Role.DATA_MINING),
        email=kwargs.pop("email", f"{username}@dmd.invalid"),
        login_access=kwargs.pop("login_access", False),
        **kwargs,
    )


class NormalizeDmdAccountsTests(TestCase):

    def run_command(self, **kwargs):
        out = io.StringIO()
        call_command("normalize_dmd_assignees", stdout=out, **kwargs)
        return out.getvalue()

    def test_a_prefixed_account_is_renamed_to_the_canonical_name(self):
        user = make_user("sc.mahek.soni", "SC - Mahek", "Soni")
        renames, merges, skipped = account_plan(User.objects.all())
        self.assertEqual([(u.id, s, c) for u, s, c in renames],
                         [(user.id, "SC - Mahek Soni", "Mahek Soni")])
        self.assertEqual((merges, skipped), ([], []))

        self.run_command()
        user.refresh_from_db()
        self.assertEqual(user.get_full_name(), "Mahek Soni")

    def test_a_seeded_duplicate_is_deactivated_and_the_real_one_kept(self):
        real = make_user("mahek.soni", "Mahek", "Soni",
                         email="mahek.soni@iq-hub.com", login_access=True)
        seeded = make_user("sc.mahek.soni", "SC - Mahek", "Soni")

        renames, merges, skipped = account_plan(User.objects.all())
        self.assertEqual(skipped, [])
        self.assertEqual(len(merges), 1)
        keeper, losers, canon = merges[0]
        # The account that can sign in wins, whichever way round they were made.
        self.assertEqual(keeper.id, real.id)
        self.assertEqual([u.id for u in losers], [seeded.id])
        self.assertEqual(canon, "Mahek Soni")

        self.run_command()
        real.refresh_from_db()
        seeded.refresh_from_db()
        self.assertEqual(real.status, "active")
        self.assertTrue(real.is_active)
        self.assertEqual(seeded.status, "inactive")
        # is_active is what the dropdown filters on, and status has to have
        # carried it, or the duplicate is still offered.
        self.assertFalse(seeded.is_active)
        # Never deleted; Ticket.created_by is SET_NULL and would lose authorship.
        self.assertTrue(User.objects.filter(pk=seeded.pk).exists())

    def test_two_real_accounts_are_reported_and_left_alone(self):
        first = make_user("mahek.soni", "Mahek", "Soni",
                          email="mahek.soni@iq-hub.com", login_access=True)
        second = make_user("mahek.soni2", "SC - Mahek", "Soni",
                           email="mahek.soni2@iq-hub.com", login_access=True)

        renames, merges, skipped = account_plan(User.objects.all())
        self.assertEqual(merges, [])
        self.assertEqual(len(skipped), 1)
        canon, members, reason = skipped[0]
        self.assertEqual(canon, "Mahek Soni")
        self.assertEqual({u.id for u in members}, {first.id, second.id})
        self.assertIn("real", reason)

        self.run_command()
        for user in (first, second):
            user.refresh_from_db()
            self.assertEqual(user.status, "active")
        second.refresh_from_db()
        self.assertEqual(second.get_full_name(), "SC - Mahek Soni")

    def test_a_non_dmd_duplicate_is_never_touched(self):
        sales = make_user("mahek.soni", "Mahek", "Soni", role=User.Role.SALES,
                          email="mahek.soni@iq-hub.com")
        seeded = make_user("sc.mahek.soni", "SC - Mahek", "Soni")

        renames, merges, skipped = account_plan(User.objects.all())
        # The real record is kept even though it is not Data Mining, and it is
        # the seeded Data Mining account that goes.
        self.assertEqual([u.id for _, losers, _ in merges for u in losers],
                         [seeded.id])
        self.assertEqual([u.id for u, _, _ in renames], [])

        self.run_command()
        sales.refresh_from_db()
        self.assertEqual(sales.status, "active")
        self.assertEqual(sales.role, User.Role.SALES)

    def test_dry_run_writes_nothing(self):
        seeded = make_user("sc.mahek.soni", "SC - Mahek", "Soni")
        make_user("mahek.soni", "Mahek", "Soni",
                  email="mahek.soni@iq-hub.com", login_access=True)
        output = self.run_command(dry_run=True)
        self.assertIn("DRY RUN", output)
        seeded.refresh_from_db()
        self.assertEqual(seeded.status, "active")
        self.assertEqual(seeded.get_full_name(), "SC - Mahek Soni")
