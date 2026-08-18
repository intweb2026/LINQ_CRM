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
    ALL_COLUMNS,
    CRM_MODULES,
    _available_fields,
    _coerce,
    _fields_for,
    _get_model,
    _header_for,
    _normalise,
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
        for tab, path, columns in (_normalise(m) for m in CRM_MODULES):
            with self.subTest(tab=tab):
                model = _get_model(path)
                self.assertTrue(
                    _fields_for(model, path, columns), f"{tab} has no columns"
                )

    def test_tab_names_are_unique(self):
        tabs = [m[0] for m in CRM_MODULES]
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
        self.assertEqual(written["headers"][:2], ["ID", "Name"])
        names = {r[written["headers"].index("Name")] for r in written["rows"]}
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
        self.assertEqual(fake.tabs_ensured, [m[0] for m in CRM_MODULES])
        self.assertEqual(set(summary), {m[0] for m in CRM_MODULES})

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

        mirror_tabs = {m[0] for m in CRM_MODULES}
        self.assertIn(dj_settings.GOOGLE_SHEET_EVENTS_TAB, mirror_tabs)


# ── Column selection ──────────────────────────────────────────────────────────

class ColumnSelectionTests(SimpleTestCase):
    """
    A narrowed tab is a full replace like any other, so the column list decides
    exactly what the sheet ends up containing. Every way of getting it wrong has
    to fail loudly rather than quietly ship a narrower sheet than intended.
    """

    PATH = "book_delegate.BookDelegate"

    def _model(self):
        return _get_model(self.PATH)

    def test_all_columns_is_every_permitted_field(self):
        model = self._model()
        self.assertEqual(
            [f.attname for f in _fields_for(model, self.PATH, ALL_COLUMNS)],
            [f.attname for f in _available_fields(model, self.PATH)],
        )

    def test_selection_is_honoured_in_the_order_given(self):
        """Sheet column order is the order the list is written in, not model order."""
        cols = ["email", "id", "first_name"]
        got = [f.attname for f in _fields_for(self._model(), self.PATH, cols)]
        self.assertEqual(got, cols)

    def test_a_foreign_key_can_be_named_either_way(self):
        for name in ("company", "company_id"):
            with self.subTest(name=name):
                got = [f.attname for f in _fields_for(self._model(), self.PATH, [name])]
                self.assertEqual(got, ["company_id"])

    def test_a_repeated_field_is_still_one_column(self):
        """A duplicate must not widen the header row past the rows beneath it."""
        cols = ["id", "company", "company_id", "id"]
        got = [f.attname for f in _fields_for(self._model(), self.PATH, cols)]
        self.assertEqual(got, ["id", "company_id"])

    def test_an_unknown_field_raises_and_names_it(self):
        with self.assertRaises(ValueError) as ctx:
            _fields_for(self._model(), self.PATH, ["id", "nope"])
        self.assertIn("nope", str(ctx.exception))

    def test_the_error_lists_what_was_available(self):
        """The message is the only field list anyone has at that moment."""
        with self.assertRaises(ValueError) as ctx:
            _fields_for(self._model(), self.PATH, ["nope"])
        self.assertIn("first_name", str(ctx.exception))

    def test_a_model_property_is_not_selectable(self):
        """full_name is a property, not a column; the mirror reads concrete fields."""
        self.assertTrue(hasattr(self._model(), "full_name"))
        with self.assertRaises(ValueError):
            _fields_for(self._model(), self.PATH, ["full_name"])

    def test_an_excluded_field_cannot_be_selected_back_in(self):
        """Naming a password column must not be a way around _EXCLUDED_FIELDS."""
        with self.assertRaises(ValueError) as ctx:
            _fields_for(User, "accounts.User", ["id", "password"])
        self.assertIn("excluded", str(ctx.exception))

    def test_an_empty_list_raises_rather_than_writing_a_bare_sheet(self):
        with self.assertRaises(ValueError):
            _fields_for(self._model(), self.PATH, [])


# ── Headers ───────────────────────────────────────────────────────────────────

class HeaderTests(SimpleTestCase):

    def test_headers_are_readable_labels(self):
        fields = _fields_for(
            _get_model("book_delegate.BookDelegate"),
            "book_delegate.BookDelegate",
            ["first_name", "delegate_payment_status"],
        )
        self.assertEqual(
            [_header_for(f) for f in fields],
            ["First Name", "Delegate Payment Status"],
        )

    def test_a_foreign_key_header_says_id(self):
        """The column holds an id, so a bare "Company" would misdescribe it."""
        field = _fields_for(_get_model("teams.Team"), "teams.Team", ["team_lead"])[0]
        self.assertEqual(field.attname, "team_lead_id")
        self.assertEqual(_header_for(field), "Team Lead ID")

    def test_id_is_not_capitalised_to_Id(self):
        pk = _fields_for(_get_model("companies.Company"), "companies.Company", ["id"])[0]
        self.assertEqual(_header_for(pk), "ID")

    def test_every_module_has_unique_headers(self):
        """Two identical headers in a sheet make a column ambiguous to read."""
        for tab, path, columns in (_normalise(m) for m in CRM_MODULES):
            with self.subTest(tab=tab):
                headers = [
                    _header_for(f) for f in _fields_for(_get_model(path), path, columns)
                ]
                self.assertEqual(len(headers), len(set(headers)))


# ── Narrowed tabs end to end ──────────────────────────────────────────────────

@override_settings(GOOGLE_SHEET_CRM_ID="test-crm-sheet")
class NarrowedTabTests(TestCase):

    def _patch(self, fake):
        return mock.patch(
            "services.google_sheets.GoogleSheetsService",
            return_value=fake,
        )

    def test_mirror_module_writes_only_the_selected_columns(self):
        Company.objects.create(name="Acme", city="London", country="UK")

        fake = _FakeSheets()
        mirror_module(fake, "Companies", "companies.Company", ["name", "country"])

        written = fake.written["Companies"]
        self.assertEqual(written["headers"], ["Name", "Country"])
        self.assertEqual(written["rows"], [["Acme", "UK"]])

    def test_rows_stay_the_width_of_the_narrowed_header(self):
        Company.objects.create(name="Acme")
        fake = _FakeSheets()
        mirror_module(fake, "Companies", "companies.Company", ["id", "name"])

        written = fake.written["Companies"]
        for row in written["rows"]:
            self.assertEqual(len(row), 2)

    def test_mirror_all_applies_each_modules_own_column_list(self):
        Company.objects.create(name="Acme")
        fake = _FakeSheets()
        modules = [("Companies", "companies.Company", ["name"])]
        with self._patch(fake):
            summary, errors = mirror_all(modules=modules)

        self.assertEqual(errors, [])
        self.assertEqual(summary, {"Companies": 1})
        self.assertEqual(fake.written["Companies"]["headers"], ["Name"])

    def test_a_bad_column_list_fails_only_its_own_tab(self):
        """One mistyped field name must not cost every other tab its refresh."""
        fake = _FakeSheets()
        modules = [
            ("Companies", "companies.Company", ["nope"]),
            ("Teams", "teams.Team", ["id", "name"]),
        ]
        with self._patch(fake):
            summary, errors = mirror_all(modules=modules)

        self.assertEqual(len(errors), 1)
        self.assertIn("Companies", errors[0])
        self.assertNotIn("Companies", fake.written)
        self.assertIn("Teams", summary)

    def test_a_two_element_module_still_means_every_column(self):
        """Callers written before column selection keep working unchanged."""
        fake = _FakeSheets()
        with self._patch(fake):
            mirror_all(modules=[("Teams", "teams.Team")])

        headers = fake.written["Teams"]["headers"]
        self.assertEqual(len(headers), len(_available_fields(_get_model("teams.Team"),
                                                             "teams.Team")))
