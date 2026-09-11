"""
The parsing that import_badge_runs does before it touches the database.

Only the four things that were actually wrong in the supplied lists, because
those are the four that will be wrong again in the next one: a stray space
between the month and the day, a time with no seconds, a name quoted around a
tab, and a title the booking does not carry. No database is needed for any of
it, which is why these are SimpleTestCase and run in a second.
"""
from django.test import SimpleTestCase

from pre_event_docs.management.commands.import_badge_runs import (
    clean, parse_stamp, without_title,
)


class StampTests(SimpleTestCase):
    def test_every_spelling_the_supplied_lists_use(self):
        for raw, expected in [
            (" Sep-2-2026 at 2:39:06 AM", (2026, 9, 2, 2, 39, 6)),
            ("Jun-19-2026 at 02:29:00 PM", (2026, 6, 19, 14, 29, 0)),
            # The stray space after the month, and a time with no seconds.
            ("May -13-2026 at 2:23 PM", (2026, 5, 13, 14, 23, 0)),
            ("Jun -01-2026 at 2:31:05 AM", (2026, 6, 1, 2, 31, 5)),
        ]:
            stamp = parse_stamp(raw)
            self.assertIsNotNone(stamp, raw)
            self.assertEqual(
                (stamp.year, stamp.month, stamp.day,
                 stamp.hour, stamp.minute, stamp.second), expected, raw)

    def test_a_stamp_it_cannot_read_is_none_rather_than_today(self):
        # The command aborts on these. Dating a run at the import would look
        # exactly like a run that was really issued today.
        for raw in ("", "   ", "not a date", "2026-09-02"):
            self.assertIsNone(parse_stamp(raw), raw)


class NameTests(SimpleTestCase):
    def test_a_name_quoted_around_a_tab_comes_back_whole(self):
        self.assertEqual(clean("Felipe \tGUERRERO"), "Felipe GUERRERO")
        self.assertEqual(clean("Paul\t Solano"), "Paul Solano")
        self.assertEqual(clean("Dr. Liviu  Mantescu"), "Dr. Liviu Mantescu")

    def test_a_title_is_dropped_only_when_a_whole_name_is_left(self):
        self.assertEqual(without_title("Dr. Liviu Mantescu"), "Liviu Mantescu")
        self.assertEqual(without_title("Prof Mustafa M. Demir"), "Mustafa M. Demir")
        # Two words are a name, not a title and a surname.
        self.assertEqual(without_title("Dr Nafie"), "Dr Nafie")
        # A trailing qualification is not a title and is left alone.
        self.assertEqual(without_title("John Horne MD"), "John Horne MD")
