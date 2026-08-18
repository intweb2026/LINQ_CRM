"""
google_sync/tests_sheet_writes.py
──────────────────────────────────
Tests for how services/google_sheets.py clears and refills a tab.

These live next to the CRM mirror tests because the mirror is what makes them
matter. Every mirrored tab is a full replace, so what the clear covers decides
whether a tab narrowed from 49 columns to 5 comes out with 5 columns or with 5
new ones followed by 44 stale ones.

Nothing here talks to Google. The API client is a stand-in that records the
requests it is handed, so the assertions are about what would be sent.
"""
from unittest import mock

from django.test import SimpleTestCase

from services.google_sheets import GoogleSheetsService


class _Exec:
    """One pending request. The real client raises from execute(), not before."""

    def __init__(self, error=None):
        self._error = error

    def execute(self):
        if self._error:
            raise RuntimeError(self._error)
        return {}


class _Values:
    def __init__(self, calls, fail_on=()):
        self.calls = calls
        self.fail_on = set(fail_on)

    def _record(self, kind, kw):
        self.calls.append((kind, kw))
        return _Exec(f"{kind} refused" if kind in self.fail_on else None)

    def clear(self, **kw):
        return self._record("clear", kw)

    def update(self, **kw):
        return self._record("update", kw)

    def append(self, **kw):
        return self._record("append", kw)


class _Spreadsheets:
    def __init__(self, values):
        self._values = values

    def values(self):
        return self._values


class _FakeApi:
    def __init__(self, spreadsheets):
        self._spreadsheets = spreadsheets

    def spreadsheets(self):
        return self._spreadsheets


def _service(calls, fail_on=()):
    """
    A GoogleSheetsService with the API replaced.

    Built with __new__ because __init__ reads a service-account key off disk,
    and none of these tests are about credentials.
    """
    svc = object.__new__(GoogleSheetsService)
    svc.spreadsheet_id = "test-sheet"
    svc.service = _FakeApi(_Spreadsheets(_Values(calls, fail_on)))
    return svc


def _ranges(calls, kind):
    return [kw["range"] for k, kw in calls if k == kind]


class ClearRangeTests(SimpleTestCase):

    def test_clear_covers_the_whole_tab(self):
        calls = []
        _service(calls).clear_sheet("Delegates")

        self.assertEqual(_ranges(calls, "clear"), ["Delegates"])

    def test_the_clear_range_is_not_column_bounded(self):
        """
        "A:Z" was the old range, and six mirrored modules are wider than 26
        columns, so it left the far end of those tabs untouched.
        """
        calls = []
        _service(calls).clear_sheet("Tickets")

        sent = _ranges(calls, "clear")[0]
        self.assertNotIn(":", sent, "a bounded range clears only the columns it names")
        self.assertNotIn("!", sent)

    def test_a_failed_clear_stops_the_write(self):
        """
        Writing into a tab that was not cleared leaves new rows on top of old
        ones, which reads as a complete sheet and is not one.
        """
        calls = []
        svc = _service(calls, fail_on={"clear"})

        with self.assertRaises(RuntimeError):
            svc.replace_data_chunked("Companies", ["Name"], iter([["Acme"]]))

        self.assertEqual([k for k, _ in calls], ["clear"])


class ReplaceDataChunkedTests(SimpleTestCase):

    def test_clears_before_writing_the_header(self):
        calls = []
        _service(calls).replace_data_chunked("Companies", ["Name"], iter([["Acme"]]))

        self.assertEqual([k for k, _ in calls][:2], ["clear", "update"])
        self.assertEqual(_ranges(calls, "update"), ["Companies!A1"])

    def test_rows_are_appended_in_chunks(self):
        calls = []
        rows = ([str(i)] for i in range(5))
        count = _service(calls).replace_data_chunked(
            "Companies", ["Name"], rows, chunk_size=2,
        )

        self.assertEqual(count, 5)
        self.assertEqual(len([k for k, _ in calls if k == "append"]), 3)

    def test_a_narrower_rewrite_leaves_nothing_of_the_wider_one(self):
        """
        The scenario column selection introduces: a tab written at full width,
        then narrowed. The clear has to reach every column the first write used.
        """
        wide = [f"col_{i}" for i in range(49)]
        calls = []
        svc = _service(calls)

        svc.replace_data_chunked("Events", wide, iter([["x"] * 49]))
        svc.replace_data_chunked("Events", ["ID", "Name"], iter([["1", "Acme"]]))

        self.assertEqual(_ranges(calls, "clear"), ["Events", "Events"])
        self.assertEqual(
            [kw["body"]["values"] for k, kw in calls if k == "update"][-1],
            [["ID", "Name"]],
        )


class SingletonTests(SimpleTestCase):

    def test_a_missing_credentials_file_leaves_the_singleton_unset(self):
        """
        Callers branch on `if not google_sheets`, so import must not raise when
        the key is absent. This is the state on a machine without credentials.
        """
        with mock.patch("os.path.exists", return_value=False):
            with self.assertRaises(FileNotFoundError):
                GoogleSheetsService()
