"""
ticket_central/tests.py
────────────────────────
Comprehensive test suite covering:
  • v1.0 / v1.1 behaviours (re-run baseline after Step-1/2 changes)
  • v1.2 new tests: TC-IMPORT-XLS-01/02, TC-IMPORT-CRASH-01/02,
    TC-FILTER-CHAR-01/02, TC-PERM-IMPORT-01/02/03, TC-BACKFILL-04/05

Target: 156+ check passes before Phase 8 can start.
"""
import logging
from datetime import date, datetime
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from teams.models import Team, TeamPermission

from .models import Ticket, TicketSequence
from .utils import (
    _coerce_row,
    _parse_date,
    _parse_int,
    build_ticket_number,
    extract_purpose_code,
    extract_type_code,
    infer_status_from_row,
)

User = get_user_model()

# ── Shared helpers ────────────────────────────────────────────────────────────

_ALL_ACCESS_ROLE_NAME = "test_all_access"


def restricted_role(name, *, view=True, create=False, update=False, delete=False,
                    module="ticket_central"):
    """A team with explicit per-module CRUD flags, for permission tests."""
    role, _ = Team.objects.get_or_create(
        name=name, defaults={"is_all_access": False},
    )
    TeamPermission.objects.update_or_create(
        team=role, module=module,
        defaults={"can_view": view, "can_create": create,
                  "can_update": update, "can_delete": delete},
    )
    return role


def all_access_role():
    """
    TicketViewSet is guarded by crm_permission("ticket_central") (views.py:45),
    which resolves through User.team. A user without one is refused on
    every request, so the default test user needs a role attached or the whole
    suite 403s before it reaches the behaviour under test.
    """
    role, _ = Team.objects.get_or_create(
        name=_ALL_ACCESS_ROLE_NAME,
        defaults={"is_all_access": True},
    )
    return role


def make_user(username, role, team=_ALL_ACCESS_ROLE_NAME, **kwargs):
    """
    `team` controls CRM permissions, independently of `role` (the legacy
    string field). Defaults to an all-access role; pass team=None for a
    deliberately permission-less user, or a Team instance for a restricted one.
    """
    u = User.objects.create_user(
        username=username,
        password="testpass123",
        role=role,
        **kwargs,
    )
    if team == _ALL_ACCESS_ROLE_NAME:
        u.team = all_access_role()
        u.save(update_fields=["team"])
    elif team is not None:
        u.team = team
        u.save(update_fields=["team"])
    Token.objects.create(user=u)
    return u


def auth(client, user):
    token = Token.objects.get(user=user)
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")


def make_ticket(**kwargs):
    defaults = dict(purpose="Test Purpose", type_of_ticket="BX", status="draft")
    defaults.update(kwargs)
    return Ticket.objects.create(**defaults)


# ── §1 Utils: _parse_date ─────────────────────────────────────────────────────

class ParseDateTests(TestCase):

    # TC-IMPORT-XLS-01 — Excel serial (float and int)
    def test_xls_serial_float_44197(self):
        """44197.0 == 2021-01-01 (the Zoho export date format)."""
        self.assertEqual(_parse_date(44197.0), date(2021, 1, 1))

    def test_xls_serial_int_44197(self):
        self.assertEqual(_parse_date(44197), date(2021, 1, 1))

    def test_none_returns_none(self):
        self.assertIsNone(_parse_date(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_parse_date(""))

    def test_space_returns_none(self):
        self.assertIsNone(_parse_date(" "))

    # TC-IMPORT-XLS-02 — 1900-leap-year-bug boundary
    def test_xls_serial_1(self):
        """Serial 1 == 1900-01-01 (Excel day 1)."""
        self.assertEqual(_parse_date(1.0), date(1900, 1, 1))

    def test_xls_serial_61(self):
        """Serial 61 == 1900-03-01 (day after the fake 1900-02-29)."""
        self.assertEqual(_parse_date(61.0), date(1900, 3, 1))

    def test_iso_format(self):
        self.assertEqual(_parse_date("2024-03-15"), date(2024, 3, 15))

    def test_iso_datetime_string(self):
        self.assertEqual(_parse_date("2024-03-15T10:30:00"), date(2024, 3, 15))

    def test_iso_datetime_z_suffix(self):
        self.assertEqual(_parse_date("2024-03-15T10:30:00Z"), date(2024, 3, 15))

    def test_dmy_slash(self):
        self.assertEqual(_parse_date("15/03/2024"), date(2024, 3, 15))

    def test_mdy_slash(self):
        self.assertEqual(_parse_date("03/15/2024"), date(2024, 3, 15))

    def test_dmy_dash(self):
        self.assertEqual(_parse_date("15-03-2024"), date(2024, 3, 15))

    def test_dd_mon_yyyy(self):
        self.assertEqual(_parse_date("15-Mar-2024"), date(2024, 3, 15))

    def test_dd_month_yyyy(self):
        self.assertEqual(_parse_date("15-March-2024"), date(2024, 3, 15))

    def test_date_object_passthrough(self):
        d = date(2024, 6, 1)
        self.assertEqual(_parse_date(d), d)

    def test_datetime_object_returns_date(self):
        dt = datetime(2024, 6, 1, 12, 0, 0)
        self.assertEqual(_parse_date(dt), date(2024, 6, 1))

    def test_trailing_tab_stripped(self):
        self.assertEqual(_parse_date("2024-03-15\t"), date(2024, 3, 15))

    def test_unrecognised_string_returns_none(self):
        self.assertIsNone(_parse_date("not-a-date"))

    def test_serial_zero_returns_none(self):
        """Serial 0 and negative are not valid dates — should return None."""
        self.assertIsNone(_parse_date(0))
        self.assertIsNone(_parse_date(-1))

    def test_bool_not_treated_as_serial(self):
        """True == 1 in Python; must not be converted to a date."""
        self.assertIsNone(_parse_date(True))


# ── §2 Utils: _parse_int ─────────────────────────────────────────────────────

class ParseIntTests(TestCase):

    def test_float_string(self):
        self.assertEqual(_parse_int("60.0"), 60)

    def test_comma_thousands(self):
        self.assertEqual(_parse_int("1,234"), 1234)

    def test_plain_int(self):
        self.assertEqual(_parse_int(42), 42)

    def test_float(self):
        self.assertEqual(_parse_int(3.7), 3)

    def test_none_returns_none(self):
        self.assertIsNone(_parse_int(None))

    def test_empty_returns_none(self):
        self.assertIsNone(_parse_int(""))

    def test_non_numeric_returns_none(self):
        self.assertIsNone(_parse_int("abc"))


# ── §3 Utils: extract codes and build_ticket_number ──────────────────────────

class TicketNumberTests(TestCase):

    def test_extract_type_with_dash(self):
        self.assertEqual(extract_type_code("Blue - BX"), "BX")

    def test_extract_type_no_dash(self):
        self.assertEqual(extract_type_code("ZID"), "ZID")

    def test_extract_type_empty(self):
        self.assertEqual(extract_type_code(""), "")

    def test_extract_type_none(self):
        self.assertEqual(extract_type_code(None), "")

    def test_extract_purpose_code(self):
        self.assertEqual(extract_purpose_code("  CEU  "), "CEU")

    def test_build_with_type(self):
        result = build_ticket_number("BX", "CEU", 10001)
        self.assertEqual(result, "BX-CEU 10001")

    def test_build_no_type(self):
        result = build_ticket_number("", "CEU", 10001)
        self.assertEqual(result, "CEU 10001")

    def test_build_no_purpose_returns_empty(self):
        self.assertEqual(build_ticket_number("BX", "", 10001), "")

    def test_build_truncates_to_50(self):
        long_purpose = "A" * 100
        result = build_ticket_number("BX", long_purpose, 10001)
        self.assertLessEqual(len(result), 50)

    def test_build_5digit_number_fits(self):
        result = build_ticket_number("BX", "CEU", 10001)
        self.assertLessEqual(len(result), 50)


# ── §4 Utils: infer_status_from_row ──────────────────────────────────────────

class InferStatusTests(TestCase):

    def test_no_fields_is_draft(self):
        self.assertEqual(infer_status_from_row({}), "draft")

    def test_mr_fields_only_is_mr_submitted(self):
        row = {"purpose": "CEU", "type_of_ticket": "BX"}
        self.assertEqual(infer_status_from_row(row), "mr_submitted")

    def test_dmd_work_fields_is_completed(self):
        row = {"purpose": "CEU", "assign_name": "Alice", "mined_count": 50}
        self.assertEqual(infer_status_from_row(row), "completed")

    def test_complete_date_alone_is_completed(self):
        row = {"complete_date": date(2024, 1, 1)}
        self.assertEqual(infer_status_from_row(row), "completed")

    def test_whitespace_only_value_not_counted(self):
        row = {"purpose": "   "}
        self.assertEqual(infer_status_from_row(row), "draft")


# ── §5 Utils: _coerce_row ─────────────────────────────────────────────────────

class CoerceRowTests(TestCase):

    # TC-IMPORT-CRASH-01 — unknown key silently dropped, no TypeError
    def test_unknown_key_dropped(self):
        """D25: bogus_key must not reach Ticket.objects.create()."""
        row = {"ticket_number": "X", "bogus_key": "y", "purpose": "CEU"}
        result = _coerce_row(row)
        self.assertNotIn("bogus_key", result)
        self.assertIn("purpose", result)

    # TC-IMPORT-CRASH-02 — warning logged for dropped key
    def test_unknown_key_logged(self):
        with self.assertLogs("ticket_central.utils", level="WARNING") as cm:
            _coerce_row({"bogus_column": "val", "purpose": "CEU"})
        self.assertTrue(any("bogus_column" in msg for msg in cm.output))

    def test_date_field_coerced(self):
        row = {"event_month_year": "2024-03-15"}
        result = _coerce_row(row)
        self.assertEqual(result["event_month_year"], date(2024, 3, 15))

    def test_excel_serial_date_coerced(self):
        """F7: float serial must be resolved to a date by _coerce_row."""
        row = {"event_month_year": 44197.0}
        result = _coerce_row(row)
        self.assertEqual(result["event_month_year"], date(2021, 1, 1))

    def test_int_field_coerced(self):
        row = {"estimate": "1,500"}
        result = _coerce_row(row)
        self.assertEqual(result["estimate"], 1500)

    def test_status_choice_coerced(self):
        row = {"status": "completed", "assign_name": "Alice"}
        result = _coerce_row(row)
        self.assertEqual(result["status"], "completed")

    def test_status_inferred_when_not_given(self):
        row = {"purpose": "CEU", "type_of_ticket": "BX"}
        result = _coerce_row(row)
        self.assertEqual(result["status"], "mr_submitted")

    def test_timestamps_come_back_under_their_internal_keys(self):
        row = {"purpose": "CEU", "created_at": "2024-01-01T00:00:00Z",
               "Modified Time": "2024-02-02 10:00:00"}
        result = _coerce_row(row)
        # Both are auto fields, so neither may reach Ticket.objects.create();
        # the importer writes them with a queryset update afterwards.
        self.assertIn("_preserved_created_at", result)
        self.assertIn("_modified_time", result)
        self.assertNotIn("created_at", result)
        self.assertNotIn("updated_at", result)

    def test_external_id_in_writable_fields(self):
        """TC-IMPORT-11 dependency: external_id must not be filtered out."""
        row = {"external_id": "ZHO-9999", "purpose": "CEU"}
        result = _coerce_row(row)
        self.assertIn("external_id", result)
        self.assertEqual(result["external_id"], "ZHO-9999")

    def test_empty_value_skipped(self):
        row = {"purpose": "CEU", "mr_comments": ""}
        result = _coerce_row(row)
        self.assertNotIn("mr_comments", result)

    def test_none_value_skipped(self):
        row = {"purpose": "CEU", "mr_comments": None}
        result = _coerce_row(row)
        self.assertNotIn("mr_comments", result)


# ── §6 Permissions ───────────────────────────────────────────────────────────

class PermissionTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        # These tests assert what each role may NOT do, so they need genuinely
        # restricted teams — the all-access default would grant everything
        # and every "cannot" assertion would fail.
        cls.admin = make_user("admin1", "admin")   # all-access
        cls.mr    = make_user("mr1", "market_research",
                              team=restricted_role("tc_mr", create=True, update=True))
        cls.dmd   = make_user("dmd1", "data_mining",
                              team=restricted_role("tc_dmd", update=True))
        cls.sales = make_user("sales1", "sales",
                              team=restricted_role("tc_sales"))
        cls.ticket = make_ticket(
            purpose="Permission Test", type_of_ticket="BX",
            created_by=cls.mr,
        )

    # Create
    def test_mr_can_create(self):
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/", {
            "purpose": "New MR Ticket", "type_of_ticket": "BX",
        }, format="json")
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)

    def test_admin_can_create(self):
        auth(self.client, self.admin)
        resp = self.client.post("/api/tickets/", {
            "purpose": "Admin Ticket", "type_of_ticket": "ZID",
        }, format="json")
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)

    def test_dmd_cannot_create(self):
        auth(self.client, self.dmd)
        resp = self.client.post("/api/tickets/", {
            "purpose": "DMD Ticket", "type_of_ticket": "BX",
        }, format="json")
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_sales_cannot_create(self):
        auth(self.client, self.sales)
        resp = self.client.post("/api/tickets/", {
            "purpose": "Sales Ticket", "type_of_ticket": "BX",
        }, format="json")
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    # List — all ticket roles can see
    def test_admin_can_list(self):
        auth(self.client, self.admin)
        self.assertEqual(self.client.get("/api/tickets/").status_code, 200)

    def test_mr_can_list(self):
        auth(self.client, self.mr)
        self.assertEqual(self.client.get("/api/tickets/").status_code, 200)

    def test_dmd_can_list(self):
        auth(self.client, self.dmd)
        self.assertEqual(self.client.get("/api/tickets/").status_code, 200)

    # Delete
    def test_admin_can_delete(self):
        auth(self.client, self.admin)
        t = make_ticket(purpose="To Delete", type_of_ticket="BX")
        resp = self.client.delete(f"/api/tickets/{t.id}/")
        self.assertEqual(resp.status_code, http_status.HTTP_204_NO_CONTENT)

    def test_mr_cannot_delete(self):
        auth(self.client, self.mr)
        resp = self.client.delete(f"/api/tickets/{self.ticket.id}/")
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_dmd_cannot_delete(self):
        auth(self.client, self.dmd)
        resp = self.client.delete(f"/api/tickets/{self.ticket.id}/")
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    # Submit MR
    def test_mr_can_submit_mr(self):
        auth(self.client, self.mr)
        t = make_ticket(purpose="P", type_of_ticket="BX", created_by=self.mr)
        resp = self.client.post(f"/api/tickets/{t.id}/submit_mr/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "mr_submitted")

    def test_dmd_cannot_submit_mr(self):
        auth(self.client, self.dmd)
        t = make_ticket(purpose="P", type_of_ticket="BX")
        resp = self.client.post(f"/api/tickets/{t.id}/submit_mr/")
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    # Submit DMD
    def test_dmd_can_submit_dmd(self):
        auth(self.client, self.dmd)
        t = make_ticket(
            purpose="P", type_of_ticket="BX",
            status="mr_submitted", mr_submitted_at=timezone.now(),
        )
        resp = self.client.post(f"/api/tickets/{t.id}/submit_dmd/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "completed")

    def test_mr_cannot_submit_dmd(self):
        auth(self.client, self.mr)
        t = make_ticket(
            purpose="P", type_of_ticket="BX",
            status="mr_submitted", mr_submitted_at=timezone.now(),
        )
        resp = self.client.post(f"/api/tickets/{t.id}/submit_dmd/")
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    # Return to MR
    def test_dmd_can_return_to_mr(self):
        auth(self.client, self.dmd)
        t = make_ticket(
            purpose="P", type_of_ticket="BX",
            status="mr_submitted", mr_submitted_at=timezone.now(),
        )
        resp = self.client.post(f"/api/tickets/{t.id}/return_to_mr/", {"reason": "Needs more data"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "returned")

    def test_mr_cannot_return_to_mr(self):
        auth(self.client, self.mr)
        t = make_ticket(
            purpose="P", type_of_ticket="BX",
            status="mr_submitted", mr_submitted_at=timezone.now(),
        )
        resp = self.client.post(f"/api/tickets/{t.id}/return_to_mr/", {"reason": "x"}, format="json")
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    # run_backfill
    def test_admin_can_run_backfill(self):
        auth(self.client, self.admin)
        resp = self.client.post("/api/tickets/run_backfill/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.get("success"))

    def test_mr_cannot_run_backfill(self):
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/run_backfill/")
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    # TC-PERM-IMPORT-01/02/03
    def test_mr_bulk_import_forbidden(self):
        """TC-PERM-IMPORT-01: MR user → 403."""
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/bulk_import/",
                                {"rows": [], "duplicate_mode": "allow_all"}, format="json")
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_dmd_bulk_import_forbidden(self):
        """TC-PERM-IMPORT-02: DMD user → 403."""
        auth(self.client, self.dmd)
        resp = self.client.post("/api/tickets/bulk_import/",
                                {"rows": [], "duplicate_mode": "allow_all"}, format="json")
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_admin_bulk_import_allowed(self):
        """TC-PERM-IMPORT-03: Admin → 200 (even with empty rows)."""
        auth(self.client, self.admin)
        resp = self.client.post("/api/tickets/bulk_import/",
                                {"rows": [{"ticket_number": "TC-TEST-001", "purpose": "P"}],
                                 "duplicate_mode": "allow_all"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["success"])


# ── §7 CRUD ───────────────────────────────────────────────────────────────────

class CRUDTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("crud_admin", "admin")
        cls.mr    = make_user("crud_mr",    "market_research")
        cls.dmd   = make_user("crud_dmd",   "data_mining")

    def test_create_valid_returns_201_with_id(self):
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/", {
            "purpose": "Valid Create", "type_of_ticket": "BX",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertIn("id", resp.data)

    def test_create_without_purpose_returns_400(self):
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/", {"type_of_ticket": "BX"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_create_without_type_returns_400(self):
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/", {"purpose": "P"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_create_400_includes_field_key(self):
        """F6: DRF field-keyed errors must be present in the 400 response."""
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/", {"type_of_ticket": "BX"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("purpose", resp.data)

    def test_create_sets_status_mr_submitted(self):
        """
        Creation goes straight to MR Submitted — there is no draft step.
        Supersedes the original D9 expectation of 'draft'; see
        TicketCreateSerializer.create() (serializers.py:121-124), which also
        stamps mr_submitted_by/at at the same moment.
        """
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/", {
            "purpose": "Straight To MR", "type_of_ticket": "BX",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        ticket = Ticket.objects.get(pk=resp.data["id"])
        self.assertEqual(ticket.status, Ticket.Status.MR_SUBMITTED)
        self.assertEqual(ticket.mr_submitted_by, self.mr)
        self.assertIsNotNone(ticket.mr_submitted_at)

    def test_create_assigns_ticket_number_when_purpose_present(self):
        """
        ticket_number is assigned AT CREATE, not overnight — supersedes D9.
        Format is '{type}-{purpose} {n}' (utils.build_ticket_number). The
        purpose is upper-cased, not embedded verbatim: extract_purpose_code
        normalises it so that case variants of one code cannot open separate
        counters.
        """
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/", {
            "purpose": "Numbered", "type_of_ticket": "BX",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        ticket = Ticket.objects.get(pk=resp.data["id"])
        self.assertNotEqual(ticket.ticket_number, "")
        self.assertTrue(ticket.ticket_number.startswith("BX-NUMBERED "))

    def test_create_without_purpose_is_rejected(self):
        """
        The serializer guards ticket_number on `if purpose_code:`, but purpose
        is validated as required first (serializers.py:104-107), so the
        no-purpose branch is unreachable through the API — the request 400s
        before create() runs. Recorded so the guard's dead branch is explicit.
        """
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/", {
            "purpose": "   ", "type_of_ticket": "BX",
        }, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("purpose", resp.data)

    def test_list_returns_200(self):
        auth(self.client, self.mr)
        resp = self.client.get("/api/tickets/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("results", resp.data)

    def test_retrieve_returns_all_fields(self):
        auth(self.client, self.mr)
        t = make_ticket(purpose="Retrieve Me", type_of_ticket="BX", created_by=self.mr)
        resp = self.client.get(f"/api/tickets/{t.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], t.id)
        # purpose is stored upper-case, so the API echoes the stored form.
        self.assertEqual(resp.data["purpose"], "RETRIEVE ME")

    def test_mr_can_update_mr_fields_in_draft(self):
        auth(self.client, self.mr)
        t = make_ticket(purpose="Old", type_of_ticket="BX", created_by=self.mr)
        resp = self.client.patch(f"/api/tickets/{t.id}/",
                                 {"purpose": "Updated", "type_of_ticket": "BX"}, format="json")
        self.assertEqual(resp.status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.purpose, "UPDATED")

    def test_mr_cannot_update_after_submitted(self):
        auth(self.client, self.mr)
        # created_by=self.mr so the refusal comes from the phase lock, which is
        # what this asserts, and not from the row being outside their scope.
        t = make_ticket(
            purpose="Submitted", type_of_ticket="BX",
            status="mr_submitted", mr_submitted_at=timezone.now(),
            created_by=self.mr,
        )
        resp = self.client.patch(f"/api/tickets/{t.id}/",
                                 {"purpose": "Attempt"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_dmd_can_update_dmd_fields_in_mr_submitted(self):
        auth(self.client, self.dmd)
        t = make_ticket(
            purpose="P", type_of_ticket="BX",
            status="mr_submitted", mr_submitted_at=timezone.now(),
        )
        resp = self.client.patch(f"/api/tickets/{t.id}/",
                                 {"assign_name": "Alice DMD"}, format="json")
        self.assertEqual(resp.status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.assign_name, "Alice DMD")

    def test_admin_can_update_any_field(self):
        auth(self.client, self.admin)
        t = make_ticket(purpose="Admin Edit", type_of_ticket="BX")
        resp = self.client.patch(f"/api/tickets/{t.id}/",
                                 {"mr_comments": "Admin note", "assign_name": "Bob"},
                                 format="json")
        self.assertEqual(resp.status_code, 200)

    def test_delete_removes_ticket(self):
        auth(self.client, self.admin)
        t = make_ticket(purpose="Delete Me", type_of_ticket="BX")
        pk = t.id
        resp = self.client.delete(f"/api/tickets/{pk}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Ticket.objects.filter(pk=pk).exists())

    def test_stats_endpoint_returns_counts(self):
        auth(self.client, self.mr)
        make_ticket(purpose="S1", type_of_ticket="BX", status="draft")
        resp = self.client.get("/api/tickets/stats/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("total", resp.data)
        self.assertIn("draft", resp.data)
        self.assertIn("completed", resp.data)


# ── §8 Phase transitions ──────────────────────────────────────────────────────

class PhaseTransitionTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("trans_admin", "admin")
        cls.mr    = make_user("trans_mr",    "market_research")
        cls.dmd   = make_user("trans_dmd",   "data_mining")

    def test_submit_mr_sets_fields(self):
        auth(self.client, self.mr)
        t = make_ticket(purpose="P", type_of_ticket="BX")
        resp = self.client.post(f"/api/tickets/{t.id}/submit_mr/")
        self.assertEqual(resp.status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.status, "mr_submitted")
        self.assertEqual(t.mr_submitted_by, self.mr)
        self.assertIsNotNone(t.mr_submitted_at)

    def test_submit_mr_from_returned_allowed(self):
        auth(self.client, self.mr)
        t = make_ticket(
            purpose="P", type_of_ticket="BX",
            status="returned", returned_at=timezone.now(),
        )
        resp = self.client.post(f"/api/tickets/{t.id}/submit_mr/")
        self.assertEqual(resp.status_code, 200)

    def test_submit_mr_from_completed_rejected(self):
        auth(self.client, self.mr)
        t = make_ticket(
            purpose="P", type_of_ticket="BX",
            status="completed",
            mr_submitted_at=timezone.now(),
            dmd_submitted_at=timezone.now(),
        )
        resp = self.client.post(f"/api/tickets/{t.id}/submit_mr/")
        self.assertEqual(resp.status_code, 400)

    def test_submit_dmd_sets_fields(self):
        auth(self.client, self.dmd)
        t = make_ticket(
            purpose="P", type_of_ticket="BX",
            status="mr_submitted", mr_submitted_at=timezone.now(),
        )
        resp = self.client.post(f"/api/tickets/{t.id}/submit_dmd/")
        self.assertEqual(resp.status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.status, "completed")
        self.assertEqual(t.dmd_submitted_by, self.dmd)
        self.assertIsNotNone(t.dmd_submitted_at)

    def test_submit_dmd_from_draft_rejected(self):
        auth(self.client, self.dmd)
        t = make_ticket(purpose="P", type_of_ticket="BX")  # draft
        resp = self.client.post(f"/api/tickets/{t.id}/submit_dmd/")
        self.assertEqual(resp.status_code, 400)

    def test_return_to_mr_sets_reason(self):
        auth(self.client, self.dmd)
        t = make_ticket(
            purpose="P", type_of_ticket="BX",
            status="mr_submitted", mr_submitted_at=timezone.now(),
        )
        resp = self.client.post(
            f"/api/tickets/{t.id}/return_to_mr/",
            {"reason": "Need better keywords"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.status, "returned")
        self.assertEqual(t.return_reason, "Need better keywords")
        self.assertEqual(t.returned_by, self.dmd)

    def test_return_to_mr_from_draft_rejected(self):
        auth(self.client, self.dmd)
        t = make_ticket(purpose="P", type_of_ticket="BX")  # draft
        resp = self.client.post(f"/api/tickets/{t.id}/return_to_mr/", {"reason": "x"}, format="json")
        self.assertEqual(resp.status_code, 400)


# ── §9 Filters ────────────────────────────────────────────────────────────────

class FilterTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("filt_admin", "admin")
        cls.mr    = make_user("filt_mr",    "market_research")
        # created_by=cls.mr on all three: these tests are about the FILTERS, and
        # the list is scoped to whoever added the row (TicketViewSet
        # .get_queryset), so an unattributed ticket is invisible to cls.mr and
        # every assertion below would fail for a reason it is not testing.
        cls.t1 = make_ticket(purpose="CEU Conference",   type_of_ticket="BX", status="draft",
                             priority="SPEX", relationship="Direct", event_code="CEU2024",
                             created_by=cls.mr)
        cls.t2 = make_ticket(purpose="Medical Summit",  type_of_ticket="ZID", status="mr_submitted",
                             mr_submitted_at=timezone.now(),
                             priority="DD", relationship="Indirect", event_code="MED2024",
                             created_by=cls.mr)
        cls.t3 = make_ticket(purpose="Tech Expo",       type_of_ticket="BX", status="completed",
                             mr_submitted_at=timezone.now(), dmd_submitted_at=timezone.now(),
                             priority="SPEX", assigned_mr="Alice Researcher",
                             created_by=cls.mr)

    def _list(self, params):
        auth(self.client, self.mr)
        return self.client.get("/api/tickets/", params)

    def test_status_filter_draft(self):
        resp = self._list({"status": "draft"})
        ids = [r["id"] for r in resp.data["results"]]
        self.assertIn(self.t1.id, ids)
        self.assertNotIn(self.t2.id, ids)

    def test_status_filter_mr_submitted(self):
        resp = self._list({"status": "mr_submitted"})
        ids = [r["id"] for r in resp.data["results"]]
        self.assertIn(self.t2.id, ids)

    # TC-FILTER-CHAR-01 — D26: priority is now CharFilter(iexact)
    def test_priority_char_filter_spex(self):
        resp = self._list({"priority": "SPEX"})
        ids = [r["id"] for r in resp.data["results"]]
        self.assertIn(self.t1.id, ids)
        self.assertNotIn(self.t2.id, ids)

    def test_priority_char_filter_case_insensitive(self):
        resp = self._list({"priority": "spex"})
        ids = [r["id"] for r in resp.data["results"]]
        self.assertIn(self.t1.id, ids)

    # TC-FILTER-CHAR-02 — D26: relationship is now CharFilter(iexact)
    def test_relationship_char_filter_direct(self):
        resp = self._list({"relationship": "Direct"})
        ids = [r["id"] for r in resp.data["results"]]
        self.assertIn(self.t1.id, ids)
        self.assertNotIn(self.t2.id, ids)

    def test_relationship_char_filter_case_insensitive(self):
        resp = self._list({"relationship": "direct"})
        ids = [r["id"] for r in resp.data["results"]]
        self.assertIn(self.t1.id, ids)

    def test_event_code_icontains(self):
        resp = self._list({"event_code": "ceu"})
        ids = [r["id"] for r in resp.data["results"]]
        self.assertIn(self.t1.id, ids)
        self.assertNotIn(self.t2.id, ids)

    def test_assigned_mr_icontains(self):
        resp = self._list({"assigned_mr": "alice"})
        ids = [r["id"] for r in resp.data["results"]]
        self.assertIn(self.t3.id, ids)

    def test_search_by_purpose(self):
        resp = self._list({"search": "Medical Summit"})
        ids = [r["id"] for r in resp.data["results"]]
        self.assertIn(self.t2.id, ids)

    def test_no_filter_returns_all(self):
        resp = self._list({})
        self.assertGreaterEqual(resp.data["count"], 3)


# ── §10 Bulk Import ───────────────────────────────────────────────────────────

class BulkImportTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("imp_admin", "admin")
        cls.mr    = make_user("imp_mr",    "market_research")

    def _import(self, rows, mode="allow_all"):
        auth(self.client, self.admin)
        return self.client.post("/api/tickets/bulk_import/", {
            "rows": rows, "duplicate_mode": mode, "batch_number": 1,
        }, format="json")

    def test_allow_all_inserts_rows(self):
        resp = self._import([
            {"ticket_number": "IMP-001", "purpose": "P1", "type_of_ticket": "BX"},
            {"ticket_number": "IMP-002", "purpose": "P2", "type_of_ticket": "BX"},
        ])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["inserted"], 2)
        self.assertEqual(resp.data["errors"], [])

    def test_skip_by_external_id_skips_existing(self):
        Ticket.objects.create(external_id="ZHO-EXT-001", purpose="Existing", type_of_ticket="BX")
        resp = self._import([
            {"external_id": "ZHO-EXT-001", "purpose": "Should Skip", "type_of_ticket": "BX"},
            {"external_id": "ZHO-EXT-002", "purpose": "Should Insert", "type_of_ticket": "BX"},
        ], mode="skip_by_external_id")
        self.assertEqual(resp.data["inserted"], 1)
        self.assertEqual(resp.data["skipped_count"], 1)

    def test_upsert_by_external_id_updates_existing(self):
        Ticket.objects.create(external_id="ZHO-UPD-001", purpose="Old", type_of_ticket="BX")
        resp = self._import([
            {"external_id": "ZHO-UPD-001", "purpose": "Updated Purpose", "type_of_ticket": "BX"},
        ], mode="upsert_by_external_id")
        self.assertEqual(resp.data["updated"], 1)
        t = Ticket.objects.get(external_id="ZHO-UPD-001")
        self.assertEqual(t.purpose, "UPDATED PURPOSE")

    def test_in_batch_duplicate_create_skipped(self):
        """F3 create path: second occurrence of same external_id in one batch is skipped."""
        resp = self._import([
            {"external_id": "ZHO-DUP-001", "purpose": "First", "type_of_ticket": "BX"},
            {"external_id": "ZHO-DUP-001", "purpose": "Second", "type_of_ticket": "BX"},
        ], mode="skip_by_external_id")
        self.assertEqual(resp.data["inserted"], 1)
        self.assertEqual(resp.data["skipped_count"], 1)
        skipped = resp.data["skipped_rows"]
        self.assertTrue(any("in_file_duplicate" in s["reason"] for s in skipped))

    def test_in_batch_duplicate_upsert_second_skipped(self):
        """F3 upsert path: second occurrence of existing key in same batch must not double-update."""
        Ticket.objects.create(
            external_id="ZHO-DUPU-001", purpose="Original", type_of_ticket="BX",
        )
        resp = self._import([
            {"external_id": "ZHO-DUPU-001", "purpose": "First Update",  "type_of_ticket": "BX"},
            {"external_id": "ZHO-DUPU-001", "purpose": "Second Update", "type_of_ticket": "BX"},
        ], mode="upsert_by_external_id")
        # Only one update should happen; second should be caught by seen_in_batch
        self.assertEqual(resp.data["updated"], 1)
        t = Ticket.objects.get(external_id="ZHO-DUPU-001")
        self.assertEqual(t.purpose, "FIRST UPDATE")

    def test_cross_batch_skip_works(self):
        """F2: external_id inserted in batch 1 should be skipped in batch 2."""
        auth(self.client, self.admin)
        # Batch 1
        self.client.post("/api/tickets/bulk_import/", {
            "rows": [{"external_id": "ZHO-CB-001", "purpose": "Batch1", "type_of_ticket": "BX"}],
            "duplicate_mode": "skip_by_external_id",
            "batch_number": 1,
        }, format="json")
        # Batch 2 — same external_id
        resp2 = self.client.post("/api/tickets/bulk_import/", {
            "rows": [{"external_id": "ZHO-CB-001", "purpose": "Batch2", "type_of_ticket": "BX"}],
            "duplicate_mode": "skip_by_external_id",
            "batch_number": 2,
        }, format="json")
        self.assertEqual(resp2.data["inserted"], 0)
        self.assertEqual(resp2.data["skipped_count"], 1)
        self.assertEqual(Ticket.objects.filter(external_id="ZHO-CB-001").count(), 1)

    # TC-IMPORT-CRASH-01 — unknown key dropped, ticket still created
    def test_unknown_key_does_not_crash_import(self):
        resp = self._import([
            {"ticket_number": "CRASH-01", "bogus_key": "garbage", "purpose": "P", "type_of_ticket": "BX"},
        ])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["inserted"], 1)
        self.assertEqual(resp.data["errors"], [])
        t = Ticket.objects.get(ticket_number="CRASH-01")
        self.assertEqual(t.purpose, "P")

    def test_row_error_does_not_abort_batch(self):
        """Per-row savepoints: a bad row shouldn't roll back the whole batch."""
        long_ext = "X" * 300  # violates max_length=50 on external_id
        resp = self._import([
            {"ticket_number": "SAVE-GOOD-1", "purpose": "Good Before", "type_of_ticket": "BX"},
            {"external_id": long_ext, "ticket_number": "SAVE-BAD",  "purpose": "Bad",  "type_of_ticket": "BX"},
            {"ticket_number": "SAVE-GOOD-2", "purpose": "Good After", "type_of_ticket": "BX"},
        ])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["inserted"], 2)
        self.assertEqual(len(resp.data["errors"]), 1)
        self.assertTrue(Ticket.objects.filter(ticket_number="SAVE-GOOD-1").exists())
        self.assertTrue(Ticket.objects.filter(ticket_number="SAVE-GOOD-2").exists())

    def test_date_field_coerced_in_import(self):
        resp = self._import([{
            "external_id": "ZHO-DATE-01",
            "purpose": "Date Test",
            "type_of_ticket": "BX",
            "event_month_year": "2024-03-15",
        }])
        self.assertEqual(resp.data["inserted"], 1)
        t = Ticket.objects.get(external_id="ZHO-DATE-01")
        self.assertEqual(t.event_month_year, date(2024, 3, 15))

    def test_excel_serial_date_coerced_in_import(self):
        """F7 end-to-end: 44197.0 must land as 2021-01-01 in the DB."""
        resp = self._import([{
            "external_id": "ZHO-XLS-DATE-01",
            "purpose": "XLS Date Test",
            "type_of_ticket": "BX",
            "event_month_year": 44197.0,
        }])
        self.assertEqual(resp.data["inserted"], 1)
        t = Ticket.objects.get(external_id="ZHO-XLS-DATE-01")
        self.assertEqual(t.event_month_year, date(2021, 1, 1))

    def test_status_inferred_for_completed_row(self):
        resp = self._import([{
            "external_id": "ZHO-STATUS-01",
            "purpose": "Status Infer",
            "type_of_ticket": "BX",
            "assign_name": "Bob DMD",
            "complete_date": "2024-01-01",
        }])
        self.assertEqual(resp.data["inserted"], 1)
        t = Ticket.objects.get(external_id="ZHO-STATUS-01")
        self.assertEqual(t.status, "completed")

    def test_explicit_status_overrides_inference(self):
        resp = self._import([{
            "external_id": "ZHO-STATUS-02",
            "purpose": "Override Status",
            "type_of_ticket": "BX",
            "status": "mr_submitted",
        }])
        t = Ticket.objects.get(external_id="ZHO-STATUS-02")
        self.assertEqual(t.status, "mr_submitted")

    def test_empty_rows_rejected(self):
        auth(self.client, self.admin)
        resp = self.client.post("/api/tickets/bulk_import/",
                                {"rows": [], "duplicate_mode": "allow_all"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_invalid_mode_rejected(self):
        auth(self.client, self.admin)
        resp = self.client.post("/api/tickets/bulk_import/", {
            "rows": [{"ticket_number": "X"}], "duplicate_mode": "bad_mode",
        }, format="json")
        self.assertEqual(resp.status_code, 400)


# ── §11 Backfill management command ──────────────────────────────────────────

class BackfillTests(TestCase):

    def _run(self, dry_run=False):
        from django.core.management import call_command
        out = StringIO()
        args = ["--dry-run"] if dry_run else []
        call_command("backfill_ticket_numbers", *args, stdout=out)
        return out.getvalue()

    def test_basic_numbering(self):
        Ticket.objects.create(purpose="CEU", type_of_ticket="BX", ticket_number="")
        output = self._run()
        t = Ticket.objects.get(purpose="CEU")
        self.assertNotEqual(t.ticket_number, "")

    def test_number_format_matches_expected(self):
        Ticket.objects.create(purpose="CEU", type_of_ticket="Blue - BX", ticket_number="")
        self._run()
        t = Ticket.objects.get(purpose="CEU")
        self.assertIn("BX", t.ticket_number)
        self.assertIn("CEU", t.ticket_number)

    def test_skips_ticket_with_no_purpose(self):
        Ticket.objects.create(purpose="", type_of_ticket="BX", ticket_number="")
        output = self._run()
        self.assertIn("Skipped", output)

    def test_dry_run_does_not_write(self):
        t = Ticket.objects.create(purpose="CEU", type_of_ticket="BX", ticket_number="")
        self._run(dry_run=True)
        t.refresh_from_db()
        self.assertEqual(t.ticket_number, "")

    def test_dry_run_output_mentions_would_number(self):
        Ticket.objects.create(purpose="CEU", type_of_ticket="BX", ticket_number="")
        output = self._run(dry_run=True)
        self.assertIn("DRY RUN", output)

    # TC-BACKFILL-04 — sequence integrity
    def test_sequence_last_number_matches_db_max(self):
        """TC-BACKFILL-04: TicketSequence.last_number == max parsed number for that purpose."""
        for _ in range(3):
            Ticket.objects.create(purpose="SEQCHECK", type_of_ticket="BX", ticket_number="")
        self._run()
        seq = TicketSequence.objects.get(purpose_key="SEQCHECK")
        numbered = Ticket.objects.filter(purpose="SEQCHECK").exclude(ticket_number="")
        nums = []
        for t in numbered:
            parts = t.ticket_number.rsplit(" ", 1)
            if len(parts) == 2:
                try:
                    nums.append(int(parts[1]))
                except ValueError:
                    pass
        self.assertEqual(seq.last_number, max(nums))

    # TC-BACKFILL-05 — idempotent
    def test_second_run_numbers_zero_tickets(self):
        """TC-BACKFILL-05: running backfill twice → second run finds nothing to number."""
        Ticket.objects.create(purpose="IDEM", type_of_ticket="BX", ticket_number="")
        self._run()
        output = self._run()
        self.assertIn("Numbered: 0", output)

    def test_already_numbered_tickets_untouched(self):
        Ticket.objects.create(purpose="ALREADY", type_of_ticket="BX", ticket_number="BX-ALREADY 10001")
        self._run()
        t = Ticket.objects.get(purpose="ALREADY")
        self.assertEqual(t.ticket_number, "BX-ALREADY 10001")

    def test_multiple_purposes_get_independent_sequences(self):
        Ticket.objects.create(purpose="CEU", type_of_ticket="BX", ticket_number="")
        Ticket.objects.create(purpose="AD",  type_of_ticket="BX", ticket_number="")
        self._run()
        t_ceu = Ticket.objects.get(purpose="CEU")
        t_ad  = Ticket.objects.get(purpose="AD")
        self.assertNotEqual(t_ceu.ticket_number, t_ad.ticket_number)
        TicketSequence.objects.get(purpose_key="CEU")
        TicketSequence.objects.get(purpose_key="AD")

    def test_run_backfill_api_returns_success(self):
        admin = make_user("bf_admin", "admin")
        from rest_framework.test import APIClient
        c = APIClient()
        auth(c, admin)
        resp = c.post("/api/tickets/run_backfill/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["success"])
