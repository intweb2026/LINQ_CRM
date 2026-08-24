"""
accounts/tests_date_tolerance.py
─────────────────────────────────
A date must never be the reason a booking is dropped, and an unreadable date
must never look like an empty one.

WHAT WENT WRONG
Five import paths each carried their own date parser and each accepted a
different subset of formats. Two of them — import_bookings_csv and
import_bookings_json — accepted exactly ONE, "%d-%b-%Y". Every one of them
returned None on anything else and reported nothing, so an invoice whose date
arrived as "08.05.2026", as "2026-05-08T10:30:00Z" or as an Excel serial landed
with a BLANK date, indistinguishable from a row the source had left undated.
import_booking_excel used pandas, which is lenient but MONTH-FIRST, so the same
"03/04/2026" meant 3 April through four importers and 4 March through the fifth.

All five now delegate to accounts.import_common.parse_import_date. These tests
pin the two properties that matter: it reads what the sources actually send, and
what it cannot read it REPORTS.

Parser-only, so SimpleTestCase; no database is touched.
"""
from datetime import date, datetime

from django.test import SimpleTestCase

from accounts.import_common import parse_import_date, parse_import_datetime


class DatesTheSourcesActuallySend(SimpleTestCase):

    def test_iso_and_its_separators(self):
        for raw in ("2026-05-08", "2026/05/08", "2026.05.08", "20260508"):
            parsed, error = parse_import_date(raw)
            self.assertIsNone(error, raw)
            self.assertEqual(parsed, date(2026, 5, 8), raw)

    def test_numeric_day_first_with_every_separator(self):
        for raw in ("08/05/2026", "08-05-2026", "08.05.2026", "08/05/26", "08-05-26"):
            parsed, error = parse_import_date(raw)
            self.assertIsNone(error, raw)
            self.assertEqual(parsed, date(2026, 5, 8), raw)

    def test_named_months_long_and_short(self):
        for raw in (
            "08-May-2026", "08-May-26", "08 May 2026", "08 May 26",
            "May 08, 2026", "May 8th, 2026", "8th May 2026",
            "08-February-2026",
        ):
            parsed, error = parse_import_date(raw)
            self.assertIsNone(error, raw)
            self.assertEqual(parsed.year, 2026, raw)

    def test_a_leading_weekday_is_stripped(self):
        parsed, error = parse_import_date("Fri, 08 May 2026")
        self.assertIsNone(error)
        self.assertEqual(parsed, date(2026, 5, 8))

    def test_a_trailing_time_of_day_is_stripped(self):
        """
        Only the ISO form reached the old handling. A slashed date carrying a
        time, and an ISO date carrying a timezone, both went to None.
        """
        for raw in (
            "2026-05-08T10:30:00Z",
            "2026-05-08 10:30:00",
            "2026-05-08T10:30:00+05:30",
            "2026-05-08T10:30:00-05:00",
            "08/05/2026 10:30",
            "08/05/2026 10:30:45",
            "2026-05-08T00:00:00.000000000",   # numpy/pandas datetime64
        ):
            parsed, error = parse_import_date(raw)
            self.assertIsNone(error, raw)
            self.assertEqual(parsed, date(2026, 5, 8), raw)

    def test_the_dirty_zoho_hyphen_spacing_still_works(self):
        """The fix this parser was originally written for, not regressed."""
        self.assertEqual(parse_import_date("20 - Dec - 2025")[0], date(2025, 12, 20))
        self.assertEqual(parse_import_date("21-February -2026")[0], date(2026, 2, 21))

    def test_a_non_breaking_space_is_not_a_parse_failure(self):
        """What a copy-paste out of a browser table leaves behind."""
        parsed, error = parse_import_date(" 2026-05-08 ")
        self.assertIsNone(error)
        self.assertEqual(parsed, date(2026, 5, 8))

    def test_excel_serials_as_number_and_as_text(self):
        """
        A CSV export of a workbook writes the serial as TEXT, and openpyxl gives
        a fraction for a cell carrying a time. Both must land on the same day.
        """
        expected = parse_import_date(45785)[0]
        self.assertIsNotNone(expected)
        for raw in (45785, "45785", 45785.5104, "45785.5104"):
            parsed, error = parse_import_date(raw)
            self.assertIsNone(error, raw)
            self.assertEqual(parsed, expected, raw)

    def test_date_and_datetime_objects_pass_through(self):
        self.assertEqual(parse_import_date(date(2026, 5, 8))[0], date(2026, 5, 8))
        self.assertEqual(
            parse_import_date(datetime(2026, 5, 8, 10, 30))[0], date(2026, 5, 8)
        )


class AmbiguousSlashedDatesStayDayFirst(SimpleTestCase):
    """
    Day-first is not a preference, it is what the dates already in the database
    MEAN — every parser this codebase has had tried "%d/%m/%Y" first. Flipping
    the pair would silently re-read existing feeds as different days.
    """

    def test_an_ambiguous_date_is_read_day_first(self):
        self.assertEqual(parse_import_date("03/04/2026")[0], date(2026, 4, 3))
        self.assertEqual(parse_import_date("03-04-2026")[0], date(2026, 4, 3))
        self.assertEqual(parse_import_date("03.04.2026")[0], date(2026, 4, 3))

    def test_an_unambiguous_date_is_read_month_first_regardless(self):
        """Day 13 does not exist as a month, so day-first is impossible here."""
        self.assertEqual(parse_import_date("12/25/2026")[0], date(2026, 12, 25))

    def test_a_four_digit_year_is_never_read_as_a_two_digit_one(self):
        """The %y formats sit last precisely so this cannot happen."""
        self.assertEqual(parse_import_date("08/05/2026")[0], date(2026, 5, 8))


class BlanksAreBlankAndFailuresAreReported(SimpleTestCase):

    def test_the_spellings_a_source_uses_for_no_value(self):
        """(None, None) — the source said nothing, which is not an error."""
        for raw in (None, "", "   ", "nan", "NaT", "None", "null", "N/A", "-", "0"):
            parsed, error = parse_import_date(raw)
            self.assertIsNone(parsed, raw)
            self.assertIsNone(error, raw)

    def test_something_unreadable_returns_a_reason_not_a_silent_none(self):
        """
        The whole point. Before this, every one of these was an unannounced
        None, so a column of bad dates was indistinguishable from an empty one.
        """
        for raw in ("hello", "2026-13-45", "not a date at all", "31/02/2026"):
            parsed, error = parse_import_date(raw)
            self.assertIsNone(parsed, raw)
            self.assertTrue(error, f"{raw!r} produced no reason")
            self.assertIn(repr(raw), error)

    def test_a_bool_is_not_a_date(self):
        """bool is an int subclass, so it must be rejected before the serial branch."""
        parsed, error = parse_import_date(True)
        self.assertIsNone(parsed)
        self.assertTrue(error)

    def test_a_number_outside_the_serial_window_is_an_error_not_a_1905_date(self):
        parsed, error = parse_import_date(2026)
        self.assertIsNone(parsed)
        self.assertTrue(error)

    def test_nothing_raises_on_anything(self):
        """
        The contract the import paths rely on. A date is never allowed to be the
        reason a booking and its delegates are thrown away.
        """
        for raw in (object(), [], {}, b"\xff", float("nan"), float("inf"), -1, 0.5):
            try:
                parse_import_date(raw)
                parse_import_datetime(raw)
            except Exception as exc:                       # pragma: no cover
                self.fail(f"parse raised {exc!r} on {raw!r}")


class DatetimesKeepTheTimeOfDay(SimpleTestCase):
    """
    Only the "Added Time" columns need this — the ones that backdate
    BookEvent.created_at. The result is naive by contract; the caller attaches
    the timezone, because only the caller knows whether its source wrote local
    time or UTC.
    """

    def test_a_time_of_day_survives(self):
        parsed, error = parse_import_datetime("08/05/2026 14:05:09")
        self.assertIsNone(error)
        self.assertEqual(parsed, datetime(2026, 5, 8, 14, 5, 9))

    def test_an_iso_timestamp_survives(self):
        self.assertEqual(
            parse_import_datetime("2026-05-08T10:30:00Z")[0],
            datetime(2026, 5, 8, 10, 30),
        )

    def test_a_serial_fraction_is_the_time_of_day(self):
        day = parse_import_date(45785)[0]
        parsed, error = parse_import_datetime(45785.5)
        self.assertIsNone(error)
        self.assertEqual(parsed, datetime(day.year, day.month, day.day, 12, 0))

    def test_a_dateless_result_is_naive_midnight_not_an_error(self):
        """A date with no time is midnight, not a failure."""
        parsed, error = parse_import_datetime("08-May-2026")
        self.assertIsNone(error)
        self.assertEqual(parsed, datetime(2026, 5, 8, 0, 0))
        self.assertIsNone(parsed.tzinfo)

    def test_the_result_is_always_naive(self):
        """A tz-aware result would shift every backdated created_at by the offset."""
        parsed, _ = parse_import_datetime("2026-05-08T10:30:00+05:30")
        self.assertIsNone(parsed.tzinfo)


class EveryImportPathUsesTheOneParser(SimpleTestCase):
    """
    Source assertions. A path that grows its own format list back is how the
    five disagreeing parsers happened in the first place, and it would not fail
    any behavioural test here — it would just quietly disagree again.
    """

    PATHS = (
        "webhooks/services.py",
        "book_event/management/commands/sync_bookings_from_sheets.py",
        "book_event/management/commands/import_bookings_csv.py",
        "book_event/management/commands/import_booking_excel.py",
    )

    def _read(self, rel):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / rel).read_text(encoding="utf-8")

    def test_no_import_path_carries_its_own_strptime_format_list(self):
        for rel in self.PATHS:
            src = self._read(rel)
            self.assertNotIn(
                "strptime", src,
                f"{rel} calls strptime directly. Date formats belong in "
                "accounts/import_common.py, which is the declared authority; a "
                "private list here is how the five parsers came to disagree.",
            )

    def test_every_import_path_imports_the_authority(self):
        for rel in self.PATHS:
            src = self._read(rel)
            self.assertIn(
                "from accounts.import_common import", src,
                f"{rel} does not import the shared date parser.",
            )

    def test_the_excel_importer_no_longer_reads_dates_through_pandas(self):
        """
        pandas is lenient but MONTH-FIRST, so this importer used to read
        "03/04/2026" as 4 March while the other four read 3 April.
        """
        src = self._read("book_event/management/commands/import_booking_excel.py")
        self.assertNotIn(
            "pd.to_datetime", src,
            "import_booking_excel is back on pandas for dates, which is "
            "month-first and disagrees with every other import path.",
        )

    def test_the_json_importer_hands_its_date_over_raw(self):
        """
        It used to pre-normalise through a one-format parser before handing the
        payload to WebhookProcessor, which THREW AWAY dates the receiving end
        can now read perfectly well.
        """
        src = self._read("book_event/management/commands/import_bookings_json.py")
        self.assertNotIn("def parse_date", src)
        self.assertIn('"Date": item.get(', src)
