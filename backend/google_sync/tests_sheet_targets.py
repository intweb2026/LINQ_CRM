"""
google_sync/tests_sheet_targets.py
───────────────────────────────────
Tests for user-defined pushes: pick a module, pick its columns, name a tab,
and a run full-replaces that tab with exactly those columns.

The case driving all of this is three columns of bookings, Delegate Name,
Delegate Email and Payment Status, which is why the fixtures below build an
invoice with delegates on it rather than a single flat row. Those three live on
two different tables, and getting them into one sheet row is the whole point of
the composed module.

Nothing here talks to Google. GoogleSheetsService is replaced by _FakeSheets.
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from companies.models import Company
from rest_framework.test import APIClient

from accounts.models import User
from teams.models import Team
from sync import catalog

from .models import GoogleSheetSyncLog, SheetSyncTarget
from .serializers import SheetSyncTargetSerializer

THREE = ["delegate_name", "delegate_email", "payment_status"]


class _FakeSheets:
    """Records what would be written instead of sending it."""

    instances = []

    def __init__(self, spreadsheet_id=None):
        self.spreadsheet_id = spreadsheet_id
        self.tabs_ensured = []
        self.written = {}
        _FakeSheets.instances.append(self)

    def ensure_tabs(self, names):
        self.tabs_ensured = list(names)
        return list(names)

    def replace_data_chunked(self, sheet_name, headers, row_iter, chunk_size=5000):
        rows = [list(r) for r in row_iter]
        self.written[sheet_name] = {"headers": list(headers), "rows": rows}
        return len(rows)


def _patch_sheets():
    _FakeSheets.instances = []
    return mock.patch("services.google_sheets.GoogleSheetsService", _FakeSheets)


def _booking(invoice_number="INV-1", **kw):
    return BookEvent.objects.create(
        invoice_number=invoice_number,
        event_code=kw.pop("event_code", "EVT-1"),
        **kw,
    )


def _delegate(invoice, first_name, email, **kw):
    return BookDelegate.objects.create(
        invoice=invoice,
        event_code=invoice.event_code,
        first_name=first_name,
        email=email,
        **kw,
    )


# ── The catalogue ─────────────────────────────────────────────────────────────

class CatalogTests(SimpleTestCase):

    def test_bookings_offers_the_three_columns_that_prompted_this(self):
        keys = {c["key"] for c in catalog.columns_for("bookings")}
        for wanted in THREE:
            self.assertIn(wanted, keys)

    def test_delegate_columns_are_not_on_the_raw_invoice_module(self):
        """
        Why "bookings" is a composed module rather than the Invoices table.
        Delegate name and email are not columns of book_event.
        """
        keys = {c["key"] for c in catalog.columns_for("invoices")}
        self.assertNotIn("delegate_name", keys)
        self.assertNotIn("delegate_email", keys)

    def test_every_module_has_a_label_and_unique_column_keys(self):
        for module in catalog.list_modules():
            with self.subTest(module=module["key"]):
                self.assertTrue(module["label"])
                keys = [c["key"] for c in module["columns"]]
                self.assertTrue(keys)
                self.assertEqual(len(keys), len(set(keys)))

    def test_an_unknown_module_is_named_in_the_error(self):
        with self.assertRaises(catalog.CatalogError) as ctx:
            catalog.columns_for("nope")
        self.assertIn("nope", str(ctx.exception))

    def test_an_unknown_column_is_named_in_the_error(self):
        with self.assertRaises(catalog.CatalogError) as ctx:
            catalog.validate("bookings", ["delegate_name", "nope"])
        self.assertIn("nope", str(ctx.exception))

    def test_an_empty_selection_is_rejected(self):
        with self.assertRaises(catalog.CatalogError):
            catalog.validate("bookings", [])

    def test_password_is_not_offered_on_the_users_module(self):
        keys = {c["key"] for c in catalog.columns_for("users")}
        self.assertNotIn("password", keys)


# ── Building the rows ─────────────────────────────────────────────────────────

class BuildRowsTests(TestCase):

    def test_headers_and_values_follow_the_selected_columns(self):
        company = Company.objects.create(name="Acme")
        inv = _booking(payment_status="Paid")
        _delegate(inv, "Ada", "ada@acme.test", last_name="Lovelace", company=company)

        headers, rows = catalog.build_rows("bookings", THREE)
        rows = list(rows)

        self.assertEqual(headers, ["Delegate Name", "Delegate Email", "Payment Status"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "ada@acme.test")
        self.assertIn("Ada", rows[0][0])
        self.assertEqual(rows[0][2], "Paid")

    def test_column_order_is_the_order_selected(self):
        inv = _booking()
        _delegate(inv, "Ada", "ada@acme.test")

        headers, _ = catalog.build_rows("bookings", ["payment_status", "delegate_name"])
        self.assertEqual(headers, ["Payment Status", "Delegate Name"])

    def test_one_row_per_delegate(self):
        inv = _booking()
        _delegate(inv, "Ada", "ada@acme.test")
        _delegate(inv, "Grace", "grace@acme.test")

        _, rows = catalog.build_rows("bookings", THREE)
        emails = {r[1] for r in rows}
        self.assertEqual(emails, {"ada@acme.test", "grace@acme.test"})

    def test_an_invoice_with_no_delegates_still_produces_a_row(self):
        """Otherwise an unassigned booking would vanish from the sheet entirely."""
        _booking(payment_status="Pending")

        _, rows = catalog.build_rows("bookings", THREE)
        rows = list(rows)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "")
        self.assertEqual(rows[0][2], "Pending")

    def test_a_repeated_column_is_still_one_column(self):
        inv = _booking()
        _delegate(inv, "Ada", "ada@acme.test")

        headers, rows = catalog.build_rows(
            "bookings", ["delegate_email", "delegate_email"],
        )
        self.assertEqual(headers, ["Delegate Email"])
        self.assertEqual([len(r) for r in rows], [1])

    def test_a_raw_module_selects_its_own_fields(self):
        Company.objects.create(name="Acme", country="UK")

        headers, rows = catalog.build_rows("companies", ["name", "country"])
        self.assertEqual(headers, ["Name", "Country"])
        self.assertEqual(list(rows), [["Acme", "UK"]])

    def test_nothing_is_built_for_an_invalid_selection(self):
        with self.assertRaises(catalog.CatalogError):
            catalog.build_rows("bookings", ["nope"])


# ── The saved target ──────────────────────────────────────────────────────────

class TargetSerializerTests(TestCase):

    def _payload(self, **kw):
        base = {
            "name": "Delegate contacts",
            "spreadsheet_id": "1AbCdEf",
            "tab_name": "Delegates",
            "module": "bookings",
            "columns": THREE,
        }
        base.update(kw)
        return base

    def test_a_pasted_url_is_reduced_to_its_id(self):
        """The address bar is what a person has; the bare id is not."""
        s = SheetSyncTargetSerializer(data=self._payload(
            spreadsheet_id="https://docs.google.com/spreadsheets/d/1AbCdEf/edit#gid=0",
        ))
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data["spreadsheet_id"], "1AbCdEf")

    def test_an_unknown_module_is_rejected_at_save_time(self):
        s = SheetSyncTargetSerializer(data=self._payload(module="nope"))
        self.assertFalse(s.is_valid())
        self.assertIn("module", s.errors)

    def test_a_column_the_module_does_not_have_is_rejected(self):
        s = SheetSyncTargetSerializer(data=self._payload(columns=["delegate_name", "nope"]))
        self.assertFalse(s.is_valid())
        self.assertIn("columns", s.errors)

    def test_no_columns_is_rejected(self):
        s = SheetSyncTargetSerializer(data=self._payload(columns=[]))
        self.assertFalse(s.is_valid())

    def test_a_blank_tab_name_is_rejected(self):
        s = SheetSyncTargetSerializer(data=self._payload(tab_name="   "))
        self.assertFalse(s.is_valid())

    def test_labels_are_returned_in_the_targets_own_order(self):
        target = SheetSyncTarget.objects.create(
            name="x", spreadsheet_id="1", tab_name="T",
            module="bookings", columns=["payment_status", "delegate_name"],
        )
        data = SheetSyncTargetSerializer(target).data
        self.assertEqual(data["column_labels"], ["Payment Status", "Delegate Name"])
        self.assertEqual(data["module_label"], "Bookings")


# ── Running one ───────────────────────────────────────────────────────────────

class RunTargetTests(TestCase):

    def setUp(self):
        self.inv = _booking(payment_status="Paid")
        _delegate(self.inv, "Ada", "ada@acme.test")
        self.target = SheetSyncTarget.objects.create(
            name="Delegate contacts",
            spreadsheet_id="1AbCdEf",
            tab_name="Delegate contacts",
            module="bookings",
            columns=THREE,
        )

    def _run(self):
        from .services import SyncOrchestrator

        with _patch_sheets():
            log = SyncOrchestrator.run_target(self.target, triggered_by="tester")
        return log, _FakeSheets.instances[-1]

    def test_it_writes_the_selected_columns_to_the_named_tab(self):
        log, fake = self._run()

        self.assertEqual(log.status, GoogleSheetSyncLog.Status.SUCCESS)
        self.assertEqual(fake.spreadsheet_id, "1AbCdEf")
        self.assertEqual(fake.tabs_ensured, ["Delegate contacts"])
        written = fake.written["Delegate contacts"]
        self.assertEqual(
            written["headers"], ["Delegate Name", "Delegate Email", "Payment Status"],
        )
        self.assertEqual(written["rows"][0][1], "ada@acme.test")

    def test_the_run_is_recorded_in_the_sync_history(self):
        log, _ = self._run()

        self.assertEqual(log.sync_type, GoogleSheetSyncLog.SyncType.SHEET_TARGET)
        self.assertEqual(log.records_processed, 1)
        self.assertEqual(log.sync_summary["module"], "bookings")
        self.assertEqual(log.sync_summary["columns"], THREE)

    def test_the_target_carries_its_own_last_run(self):
        self._run()
        self.target.refresh_from_db()

        self.assertEqual(self.target.last_status, SheetSyncTarget.Status.SUCCESS)
        self.assertEqual(self.target.records_synced, 1)
        self.assertIsNotNone(self.target.last_synced_at)
        self.assertEqual(self.target.last_error, "")

    def test_a_failure_is_recorded_on_both_the_log_and_the_target(self):
        from .services import SyncOrchestrator

        boom = mock.patch(
            "services.google_sheets.GoogleSheetsService",
            side_effect=RuntimeError("no credentials"),
        )
        with boom:
            log = SyncOrchestrator.run_target(self.target, triggered_by="tester")

        self.target.refresh_from_db()
        self.assertEqual(log.status, GoogleSheetSyncLog.Status.FAILED)
        self.assertIn("no credentials", log.error_message)
        self.assertEqual(self.target.last_status, SheetSyncTarget.Status.FAILED)
        self.assertIn("no credentials", self.target.last_error)

    def test_a_stale_column_fails_the_run_rather_than_writing_a_short_sheet(self):
        """A target saved before a column was renamed must not quietly shrink."""
        from .services import SyncOrchestrator

        SheetSyncTarget.objects.filter(pk=self.target.pk).update(columns=["gone"])
        self.target.refresh_from_db()

        with _patch_sheets():
            log = SyncOrchestrator.run_target(self.target)

        self.assertEqual(log.status, GoogleSheetSyncLog.Status.FAILED)
        self.assertEqual(_FakeSheets.instances, [])


# ── Inside Sync all ───────────────────────────────────────────────────────────

class FullSyncRunsPushesTests(TestCase):
    """
    A push is a sync like any other, so "Sync all" has to carry it.

    Before this, a push only ever moved when somebody pressed its own button;
    the scheduled full_sync wrote bookings and events and left every push
    untouched, so a sheet could sit stale for days with the page reporting a
    successful sync.
    """

    def setUp(self):
        inv = _booking(payment_status="Paid")
        _delegate(inv, "Ada", "ada@acme.test")
        self.enabled = SheetSyncTarget.objects.create(
            name="Delegate contacts", spreadsheet_id="1AbCdEf",
            tab_name="Delegate contacts", module="bookings", columns=THREE,
        )
        self.disabled = SheetSyncTarget.objects.create(
            name="Paused push", spreadsheet_id="1AbCdEf",
            tab_name="Paused", module="bookings", columns=THREE,
            is_enabled=False,
        )

    def _full_sync(self):
        from .services import SyncOrchestrator

        with _patch_sheets(),                 mock.patch("services.google_sheets.google_sheets", object()),                 mock.patch("sync.bookings_sync.sync_bookings"),                 mock.patch("sync.events_sync.sync_events"):
            return SyncOrchestrator.run(sync_type="full_sync", triggered_by="tester")

    def test_sync_all_writes_every_enabled_push(self):
        log = self._full_sync()
        self.enabled.refresh_from_db()

        self.assertEqual(self.enabled.last_status, SheetSyncTarget.Status.SUCCESS)
        self.assertEqual(log.sync_summary["pushes"], {"Delegate contacts": 1})
        self.assertEqual(log.records_processed, 1)

    def test_a_disabled_push_is_left_alone(self):
        self._full_sync()
        self.disabled.refresh_from_db()

        self.assertEqual(self.disabled.last_status, SheetSyncTarget.Status.NEVER)
        self.assertNotIn("Paused", [f.written for f in _FakeSheets.instances][0])

    def test_each_push_still_keeps_its_own_log_row(self):
        self._full_sync()

        self.assertTrue(
            GoogleSheetSyncLog.objects.filter(
                sync_type=GoogleSheetSyncLog.SyncType.SHEET_TARGET,
                sheet_name="Delegate contacts",
            ).exists()
        )

    def test_a_failing_push_makes_the_whole_sync_partial(self):
        from .services import SyncOrchestrator

        with mock.patch("services.google_sheets.google_sheets", object()),                 mock.patch("sync.bookings_sync.sync_bookings"),                 mock.patch("sync.events_sync.sync_events"),                 mock.patch("services.google_sheets.GoogleSheetsService",
                           side_effect=RuntimeError("no credentials")):
            log = SyncOrchestrator.run(sync_type="full_sync", triggered_by="tester")

        self.assertEqual(log.status, GoogleSheetSyncLog.Status.FAILED)
        self.assertIn("Push Delegate contacts", log.error_message)


# ── Over the wire ─────────────────────────────────────────────────────────────

class TargetApiTests(TestCase):

    def setUp(self):
        # An all-access TEAM, not merely role="admin". These endpoints moved from
        # IsAdminRole to crm_permission("google_sync") when Google Sync got its own
        # module, and under that gate `role` grants nothing on its own — access
        # comes from the team's grid, which is all-True for an all-access team.
        # Every other module's tests are set up the same way.
        self.admin = User.objects.create_user(
            username="admin1", password="pw", role="admin", is_staff=True,
        )
        self.admin.team = Team.objects.create(name="gsync_admin", is_all_access=True)
        self.admin.save()
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

        inv = _booking(payment_status="Paid")
        _delegate(inv, "Ada", "ada@acme.test")

    def test_the_catalogue_is_served_to_the_picker(self):
        resp = self.client.get("/api/google-sync/catalog/")

        self.assertEqual(resp.status_code, 200)
        modules = {m["key"]: m for m in resp.json()["modules"]}
        self.assertIn("bookings", modules)
        keys = {c["key"] for c in modules["bookings"]["columns"]}
        self.assertTrue(set(THREE) <= keys)

    def test_a_target_can_be_created_from_a_sheet_url(self):
        resp = self.client.post("/api/google-sync/targets/", {
            "name": "Delegate contacts",
            "spreadsheet_id": "https://docs.google.com/spreadsheets/d/1AbCdEf/edit",
            "tab_name": "Delegate contacts",
            "module": "bookings",
            "columns": THREE,
        }, format="json")

        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["spreadsheet_id"], "1AbCdEf")
        self.assertEqual(
            resp.json()["column_labels"],
            ["Delegate Name", "Delegate Email", "Payment Status"],
        )

    def test_the_creator_is_recorded(self):
        self.client.post("/api/google-sync/targets/", {
            "name": "x", "spreadsheet_id": "1AbCdEf", "tab_name": "T",
            "module": "bookings", "columns": THREE,
        }, format="json")

        self.assertEqual(SheetSyncTarget.objects.get().created_by, self.admin)

    def test_two_targets_cannot_own_one_tab(self):
        """
        Each run full-replaces the tab, so a second target on it would silently
        undo the first every time it ran.
        """
        body = {
            "name": "first", "spreadsheet_id": "1AbCdEf", "tab_name": "Shared",
            "module": "bookings", "columns": THREE,
        }
        self.client.post("/api/google-sync/targets/", body, format="json")
        clash = self.client.post(
            "/api/google-sync/targets/", dict(body, name="second"), format="json",
        )

        self.assertEqual(clash.status_code, 400)
        self.assertEqual(SheetSyncTarget.objects.count(), 1)

    def test_running_a_target_writes_the_sheet(self):
        created = self.client.post("/api/google-sync/targets/", {
            "name": "x", "spreadsheet_id": "1AbCdEf", "tab_name": "T",
            "module": "bookings", "columns": THREE,
        }, format="json").json()

        with _patch_sheets():
            resp = self.client.post(f"/api/google-sync/targets/{created['id']}/run/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["log"]["records_processed"], 1)
        self.assertEqual(_FakeSheets.instances[-1].written["T"]["rows"][0][1],
                         "ada@acme.test")

    def test_a_disabled_target_will_not_run(self):
        target = SheetSyncTarget.objects.create(
            name="x", spreadsheet_id="1AbCdEf", tab_name="T",
            module="bookings", columns=THREE, is_enabled=False,
        )

        with _patch_sheets():
            resp = self.client.post(f"/api/google-sync/targets/{target.id}/run/")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(_FakeSheets.instances, [])

    def test_a_run_is_refused_while_another_sync_holds_the_lock(self):
        from django.core.cache import cache

        from .services import SYNC_LOCK_KEY

        target = SheetSyncTarget.objects.create(
            name="x", spreadsheet_id="1AbCdEf", tab_name="T",
            module="bookings", columns=THREE,
        )
        cache.add(SYNC_LOCK_KEY, "true", 60)
        try:
            resp = self.client.post(f"/api/google-sync/targets/{target.id}/run/")
        finally:
            cache.delete(SYNC_LOCK_KEY)

        self.assertEqual(resp.status_code, 409)

    def test_a_non_admin_is_kept_out(self):
        client = APIClient()
        client.force_authenticate(
            User.objects.create_user(username="sales1", password="pw", role="sales")
        )

        self.assertEqual(client.get("/api/google-sync/catalog/").status_code, 403)
        self.assertEqual(client.get("/api/google-sync/targets/").status_code, 403)
