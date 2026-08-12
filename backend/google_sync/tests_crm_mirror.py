"""
google_sync/tests_crm_mirror.py
────────────────────────────────
Tests for the CRM → "CRM data" spreadsheet mirror (sync/crm_mirror.py).

No test here talks to Google. The Sheets client is replaced by _FakeSheets,
which records the calls it receives, so assertions are about what *would* be
written — the header row, the cell values, and which tabs got replaced.

The security assertion (User.password never reaches a sheet) is the reason this
file leads with field selection rather than row content.
"""
import datetime
import decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from companies.models import Company
from sync import crm_mirror
from sync.crm_mirror import (
    CRM_MODULES,
    _coerce,
    _fields_for,
    _get_model,
    mirror_all,
    mirror_module,
)

User = get_user_model()


class _FakeSheets:
    """Stand-in for GoogleSheetsService. Records writes instead of sending them."""

    def __init__(self, fail_tabs=()):
        self.tabs_ensured = []
        self.written = {}          # tab -> {"headers": [...], "rows": [[...]]}
        self.fail_tabs = set(fail_tabs)

    def ensure_tabs(self, names):
        self.tabs_ensured = list(names)
        return list(names)

    def replace_data_chunked(self, sheet_name, headers, row_iter, chunk_size=5000):
        if sheet_name in self.fail_tabs:
            raise RuntimeError(f"boom: {sheet_name}")
        rows = [list(r) for r in row_iter]
        self.written[sheet_name] = {"headers": list(headers), "rows": rows}
        return len(rows)


# ── Field selection ───────────────────────────────────────────────────────────

class FieldSelectionTests(SimpleTestCase):

    def test_password_is_never_mirrored(self):
        """A password hash in the sheet would be a credential leak."""
        mirrored = [f.attname for f in _fields_for(User, "accounts.User")]
        self.assertIn("password", [f.attname for f in User._meta.fields],
                      "guard is meaningless if the model has no password field")
        self.assertNotIn("password", mirrored)

    def test_every_configured_module_resolves(self):
        for tab, path in CRM_MODULES:
            with self.subTest(tab=tab):
                model = _get_model(path)
                self.assertTrue(_fields_for(model, path), f"{tab} has no columns")

    def test_tab_names_are_unique(self):
        tabs = [t for t, _ in CRM_MODULES]
        self.assertEqual(len(tabs), len(set(tabs)))

    def test_foreign_keys_mirror_as_id_columns(self):
        """Raw-table shape: FKs are joinable ids, not repr() of the related row."""
        cols = [f.attname for f in _fields_for(_get_model("teams.Team"), "teams.Team")]
        self.assertIn("team_lead_id", cols)
        self.assertNotIn("team_lead", cols)


# ── Value coercion ────────────────────────────────────────────────────────────

class CoerceTests(SimpleTestCase):

    def test_none_becomes_empty_string(self):
        self.assertEqual(_coerce(None), "")

    def test_booleans_are_explicit(self):
        # Bare True/False would land in Sheets as 1/0 and read as numbers.
        self.assertEqual(_coerce(True), "TRUE")
        self.assertEqual(_coerce(False), "FALSE")

    def test_bool_is_checked_before_int(self):
        """bool subclasses int — order matters in _coerce."""
        self.assertNotIn(_coerce(True), (1, "1"))

    def test_decimal_becomes_float(self):
        self.assertEqual(_coerce(decimal.Decimal("1234.50")), 1234.50)

    def test_dates_are_iso(self):
        self.assertEqual(_coerce(datetime.date(2026, 8, 12)), "2026-08-12")
        self.assertEqual(
            _coerce(datetime.datetime(2026, 8, 12, 5, 30, 0)),
            "2026-08-12 05:30:00",
        )

    def test_json_fields_are_serialised(self):
        self.assertEqual(_coerce({"a": 1}), '{"a": 1}')
        self.assertEqual(_coerce([1, 2]), "[1, 2]")

    def test_numbers_pass_through(self):
        self.assertEqual(_coerce(42), 42)


# ── Row streaming ─────────────────────────────────────────────────────────────

class MirrorModuleTests(TestCase):

    def test_writes_header_row_and_every_record(self):
        Company.objects.create(name="Acme", city="London", country="UK")
        Company.objects.create(name="Globex", city="Pune", country="India")

        fake = _FakeSheets()
        count = mirror_module(fake, "Companies", "companies.Company")

        self.assertEqual(count, 2)
        written = fake.written["Companies"]
        self.assertEqual(written["headers"][:2], ["id", "name"])
        names = {r[written["headers"].index("name")] for r in written["rows"]}
        self.assertEqual(names, {"Acme", "Globex"})

    def test_every_row_matches_header_width(self):
        Company.objects.create(name="Acme")
        fake = _FakeSheets()
        mirror_module(fake, "Companies", "companies.Company")

        written = fake.written["Companies"]
        for row in written["rows"]:
            self.assertEqual(len(row), len(written["headers"]))

    def test_empty_table_still_writes_headers(self):
        fake = _FakeSheets()
        count = mirror_module(fake, "Companies", "companies.Company")

        self.assertEqual(count, 0)
        self.assertTrue(fake.written["Companies"]["headers"])


# ── Orchestration ─────────────────────────────────────────────────────────────

@override_settings(GOOGLE_SHEET_CRM_ID="test-crm-sheet")
class MirrorAllTests(TestCase):

    def _patch(self, fake):
        return mock.patch(
            "services.google_sheets.GoogleSheetsService",
            return_value=fake,
        )

    def test_creates_a_tab_for_every_module(self):
        fake = _FakeSheets()
        with self._patch(fake):
            summary, errors = mirror_all()

        self.assertEqual(errors, [])
        self.assertEqual(fake.tabs_ensured, [t for t, _ in CRM_MODULES])
        self.assertEqual(set(summary), {t for t, _ in CRM_MODULES})

    def test_one_failing_tab_does_not_stop_the_rest(self):
        """A single bad module must not leave every other tab stale."""
        fake = _FakeSheets(fail_tabs={"Tickets"})
        with self._patch(fake):
            summary, errors = mirror_all()

        self.assertEqual(len(errors), 1)
        self.assertIn("Tickets", errors[0])
        self.assertNotIn("Tickets", summary)
        self.assertIn("Companies", summary)

    def test_honours_an_explicit_module_subset(self):
        fake = _FakeSheets()
        subset = [("Companies", "companies.Company")]
        with self._patch(fake):
            summary, errors = mirror_all(modules=subset)

        self.assertEqual(errors, [])
        self.assertEqual(list(summary), ["Companies"])
        self.assertEqual(fake.tabs_ensured, ["Companies"])

    def test_uses_the_crm_sheet_id_not_the_bookings_one(self):
        """The mirror must not write into the bookings/events spreadsheet."""
        fake = _FakeSheets()
        with self.settings(GOOGLE_SHEET_CRM_ID="crm-sheet", GOOGLE_SHEET_ID="other"):
            with self._patch(fake) as ctor:
                mirror_all(modules=[("Companies", "companies.Company")])
        ctor.assert_called_once_with(spreadsheet_id="crm-sheet")

    def test_refuses_to_run_without_an_explicit_crm_sheet_id(self):
        """
        Falling back to GOOGLE_SHEET_ID would put this mirror's "Events" tab in
        the same spreadsheet as events_sync.py's, and the two would overwrite
        each other. Refusing is the safe behaviour.
        """
        fake = _FakeSheets()
        with self.settings(GOOGLE_SHEET_CRM_ID="", GOOGLE_SHEET_ID="bookings-sheet"):
            with self._patch(fake):
                with self.assertRaises(RuntimeError) as ctx:
                    mirror_all(modules=[("Companies", "companies.Company")])

        self.assertIn("GOOGLE_SHEET_CRM_ID", str(ctx.exception))
        self.assertEqual(fake.written, {}, "nothing may be written on refusal")

    def test_mirror_tab_names_collide_with_the_bookings_push(self):
        """Documents *why* the guard above exists — not a hypothetical clash."""
        from django.conf import settings as dj_settings

        mirror_tabs = {t for t, _ in CRM_MODULES}
        self.assertIn(dj_settings.GOOGLE_SHEET_EVENTS_TAB, mirror_tabs)
