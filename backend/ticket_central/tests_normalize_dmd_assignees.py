"""
ticket_central/tests_normalize_dmd_assignees.py
────────────────────────────────────────────────
One spelling per person, and never one person per two people.

WHAT THIS PROTECTS
normalize_dmd_assignees rewrote 12,358 live rows, so the risk is not that it
does too little; it is that a later edit widens the prefix pattern or reaches
for fuzzy matching and silently folds two colleagues into one. The live data
contains a proven trap. Riddhi Patel and Siddhi Patel are 0.63 similar, hold
1,181 tickets between them, and DO appear together on one ticket, so they are
two people; any similarity-based pass collapses them.

So the assertions run in both directions. The variants that must merge, merge;
the names that must stay apart, stay apart.
"""
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from teams.models import Team
from ticket_central.management.commands.normalize_dmd_assignees import (
    canonical, rewrite,
)
from ticket_central.models import Ticket

User = get_user_model()


class CanonicalTests(TestCase):
    def test_prefixes_and_spacing_collapse(self):
        for raw in ("SC - Mahek Soni", "SC- Mahek Soni", "Sc-Mahek Soni",
                    "  Mahek   Soni  ", "LX - Mahek Soni"):
            with self.subTest(raw=raw):
                self.assertEqual(canonical(raw), "Mahek Soni")

    def test_confirmed_typos_fold(self):
        self.assertEqual(canonical("Shradha Jadav"), "Shraddha Jadav")
        self.assertEqual(canonical("Bharati Mahajan"), "Bharti Mahajan")
        self.assertEqual(canonical("Aashtha Singh"), "Aastha Singh")
        self.assertEqual(canonical("Drashti Panchal"), "Dhrashti Panchal")
        # Reached through the prefix strip as well, not only bare.
        self.assertEqual(canonical("SC - Bharati Chauhan"), "Bharti Chauhan")

    def test_different_people_are_never_merged(self):
        """
        The trap. These two are 0.63 trigram-similar and share a ticket in the
        live data, so they are separate humans. Anything that makes this pass
        assert equal has broken the data.
        """
        self.assertNotEqual(canonical("Riddhi Patel"), canonical("Siddhi Patel"))
        self.assertNotEqual(canonical("Janki Solanki"), canonical("Janvi Solanki"))
        # The hand-typed disambiguators are load-bearing; somebody wrote them to
        # tell two Anjalis apart.
        self.assertNotEqual(
            canonical("Anjali Solanki"), canonical("Anjali Solanki (New)"),
        )
        self.assertNotEqual(
            canonical("Anjali Solanki"), canonical("Anjali Solanki 2"),
        )
        self.assertNotEqual(
            canonical("Priya Prajapati"), canonical("Priya H Prajapati"),
        )

    def test_casing_is_left_alone(self):
        """Title-casing would turn KR into Kr, which matches no row."""
        self.assertEqual(canonical("SC-KR"), "KR")

    def test_shared_assignments_keep_every_name(self):
        self.assertEqual(
            rewrite("SC - Bharti Chauhan / Himani Gandhi"),
            "Bharti Chauhan / Himani Gandhi",
        )
        # Two prefixes in one cell, both stripped.
        self.assertEqual(
            rewrite("LX - Antara Vaidya / SC - Mahek Soni"),
            "Antara Vaidya / Mahek Soni",
        )
        # Already canonical, so no write at all.
        self.assertIsNone(rewrite("Mahek Soni"))
        self.assertIsNone(rewrite(""))


class NormalizeCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.team = Team.objects.create(name="DMD Team", is_all_access=False)

    def setUp(self):
        # The command writes an undo file, and its default lands in whatever
        # directory the runner started in. Pointed at a temp dir so a test run
        # does not drop JSON into the repo.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.undo = str(Path(self.tmp.name) / "undo.json")

    def normalize(self, *args):
        call_command("normalize_dmd_assignees", *args, verbosity=0,
                     undo_file=self.undo)

    def test_it_rewrites_both_columns_and_renames_the_account(self):
        a = Ticket.objects.create(purpose="PIK", assign_name="SC - Mahek Soni")
        b = Ticket.objects.create(purpose="PIK", assign_name_lx2="SC- Mahek Soni")
        combo = Ticket.objects.create(
            purpose="PIK", assign_name="SC - Bharti Chauhan / Himani Gandhi",
        )
        user = User.objects.create_user(
            username="bc", email="bc@example.com", first_name="Bharati",
            last_name="Chauhan", role=User.Role.DATA_MINING, team=self.team,
        )
        self.normalize()
        a.refresh_from_db(); b.refresh_from_db(); combo.refresh_from_db()
        self.assertEqual(a.assign_name, "Mahek Soni")
        self.assertEqual(b.assign_name_lx2, "Mahek Soni")
        self.assertEqual(combo.assign_name, "Bharti Chauhan / Himani Gandhi")
        user.refresh_from_db()
        self.assertEqual(user.get_full_name(), "Bharti Chauhan")
        # The rename must not cost them their login.
        self.assertEqual(user.email, "bc@example.com")

    def test_dry_run_writes_nothing(self):
        t = Ticket.objects.create(purpose="PIK", assign_name="SC - Mahek Soni")
        self.normalize("--dry-run")
        t.refresh_from_db()
        self.assertEqual(t.assign_name, "SC - Mahek Soni")

    def test_normalize_then_seed_yields_one_account_per_person(self):
        """
        The two commands are a pipeline, and this is the whole point of it.
        Seeding the raw spellings produced three accounts for Mahek Soni;
        normalising first produces one.
        """
        for _ in range(15):
            Ticket.objects.create(purpose="PIK", assign_name="SC - Mahek Soni")
        for _ in range(5):
            Ticket.objects.create(purpose="PIK", assign_name="Mahek Soni")
        self.normalize()
        call_command("seed_dmd_assignees", verbosity=0)
        mahek = [
            u for u in User.objects.filter(role=User.Role.DATA_MINING)
            if "MAHEK" in (u.get_full_name() or "").upper()
        ]
        self.assertEqual(len(mahek), 1, [u.get_full_name() for u in mahek])
        self.assertEqual(mahek[0].get_full_name(), "Mahek Soni")
