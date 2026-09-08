"""
events/tests_import_events_xlsx.py
──────────────────────────────────
The two pieces of import_events_xlsx that are not a straight assignment.

edition_code is the reason the import is safe to run at all. The sheet repeats an
event code across years AND within a year, while events.event_code is unique and
104 of the sheet's codes name live 2026 editions. If it ever stops being
deterministic, a second run mints a parallel set of codes and silently doubles
the catalogue.
"""
from datetime import date

from django.test import SimpleTestCase

from events.management.commands.import_events_xlsx import edition_code, _text


class EditionCodeTests(SimpleTestCase):
    def test_initials_come_off_and_the_year_goes_on(self):
        seen = set()
        codes = [
            edition_code("DDU - PT", "DDU", 2025, date(2025, 2, 3), seen),
            edition_code("DDU - PT", "DDU", 2024, date(2024, 2, 5), seen),
        ]
        self.assertEqual(codes, ["DDU 25", "DDU 24"])
        # Every live 2026 code carries its initials, so a minted code cannot
        # collide with one. That is the whole reason this scheme is safe.
        self.assertNotIn("DDU - PT", codes)

    def test_same_family_twice_in_one_year_takes_the_month_then_a_counter(self):
        seen = set()
        self.assertEqual(
            [edition_code("DAU - AD", "DAU", 2025, d, seen) for d in
             (date(2025, 3, 26), date(2025, 6, 11), date(2025, 11, 19))],
            ["DAU 25", "DAU 25 JUN", "DAU 25 NOV"],
        )
        # The counter is only for a family that ran twice in the SAME month.
        self.assertEqual(edition_code("DAU - AD", "DAU", 2025, date(2025, 11, 26), seen),
                         "DAU 25 #2")

    def test_minting_ignores_the_database_so_a_re_run_updates_in_place(self):
        rows = [("AFS - JS", "AFS", 2025, date(2025, 2, 10)),
                ("AFS - JS", "AFS", 2024, date(2024, 2, 12))]
        first = [edition_code(*r, set()) for r in rows]
        second, seen = [], set()
        for row in rows:
            second.append(edition_code(*row, seen))
        self.assertEqual(first, ["AFS 25", "AFS 24"])
        self.assertEqual(first, second)

    def test_a_blank_base_code_falls_back_to_the_sheet_code(self):
        self.assertEqual(edition_code("ODD ONE", "", 2023, date(2023, 5, 1), set()),
                         "ODD ONE 23")


class TextTests(SimpleTestCase):
    def test_placeholders_collapse_to_blank(self):
        # Stored verbatim these print as owner names and suppress the team
        # fallback in events/serializers.py. Blank also means no account is
        # created for them, which is the point of the list.
        for raw in ("N/A", " n/a ", "?", "-", "—", "TBC", None, ""):
            self.assertEqual(_text(raw), "", raw)

    def test_real_values_survive_with_whitespace_normalised(self):
        self.assertEqual(_text("  Bruce   Yanez "), "Bruce Yanez")
        self.assertEqual(_text("Going Ahead"), "Going Ahead")
