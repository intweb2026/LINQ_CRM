"""
dataapi/tests_dataapi.py
─────────────────────────
Covers the security boundary of the Data API, which is the part of it that
cannot be checked by eye: who gets in, who is refused, and with what status.
"""
import datetime

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from dataapi.models import DataApiKey
from events.models import Event

HEADER = "HTTP_X_DATA_API_KEY"


class DataApiKeyModelTests(TestCase):
    def test_raw_key_is_prefixed_hashed_and_not_stored(self):
        key, raw = DataApiKey.create_key(name="Test Key")
        self.assertTrue(raw.startswith("dapi_"))
        self.assertEqual(key.key_hash, DataApiKey.hash_key(raw))
        self.assertNotIn(raw, key.key_hash)
        self.assertEqual(key.key_preview, raw[:8] + "..." + raw[-4:])

    def test_empty_scopes_means_all_resources(self):
        key, _ = DataApiKey.create_key(name="Unscoped")
        for resource in ("bookings", "delegates", "events"):
            self.assertTrue(key.has_scope(resource))

    def test_scoped_key_refuses_other_resources(self):
        key, _ = DataApiKey.create_key(name="Scoped", scopes=["bookings"])
        self.assertTrue(key.has_scope("bookings"))
        self.assertFalse(key.has_scope("delegates"))

    def test_expired_and_inactive_keys_are_invalid(self):
        expired, _ = DataApiKey.create_key(
            name="Expired", expires_at=timezone.now() - datetime.timedelta(days=1))
        self.assertFalse(expired.is_valid())
        disabled, _ = DataApiKey.create_key(name="Disabled")
        disabled.is_active = False
        self.assertFalse(disabled.is_valid())


class DataApiEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Event.objects.create(event_code="TESTEV", official_event_name="Test Event",
                             event_date=datetime.date(2026, 9, 1))
        invoice = BookEvent.objects.create(
            invoice_number="INV-TEST-1", event_code="TESTEV-26",
            company_name="ACME", payment_status="Paid", total_amount=100,
        )
        BookDelegate.objects.create(
            invoice=invoice, event_code="TESTEV-26",
            first_name="Ada", last_name="Lovelace", email="ada@example.com",
        )

    def setUp(self):
        self.key, self.raw = DataApiKey.create_key(name="Full", scopes=[])

    def _get(self, path, raw_key=None):
        kwargs = {HEADER: raw_key} if raw_key else {}
        return self.client.get(path, **kwargs)

    def test_no_key_returns_401(self):
        self.assertEqual(self._get("/api/data/bookings/").status_code, 401)

    def test_invalid_key_returns_401(self):
        self.assertEqual(self._get("/api/data/bookings/", "dapi_nonsense").status_code, 401)

    def test_wrong_prefix_returns_401(self):
        self.assertEqual(self._get("/api/data/bookings/", "crm_live_abc").status_code, 401)

    def test_deactivated_key_returns_401(self):
        self.key.is_active = False
        self.key.save(update_fields=["is_active"])
        self.assertEqual(self._get("/api/data/bookings/", self.raw).status_code, 401)

    def test_expired_key_returns_401(self):
        self.key.expires_at = timezone.now() - datetime.timedelta(minutes=1)
        self.key.save(update_fields=["expires_at"])
        self.assertEqual(self._get("/api/data/bookings/", self.raw).status_code, 401)

    def test_out_of_scope_resource_returns_403(self):
        key, raw = DataApiKey.create_key(name="Bookings only", scopes=["bookings"])
        self.assertEqual(self._get("/api/data/bookings/", raw).status_code, 200)
        self.assertEqual(self._get("/api/data/delegates/", raw).status_code, 403)

    def test_envelope_shape_and_content(self):
        for resource in ("bookings", "delegates", "events"):
            with self.subTest(resource=resource):
                resp = self._get(f"/api/data/{resource}/", self.raw)
                self.assertEqual(resp.status_code, 200)
                body = resp.json()
                self.assertEqual(body["resource"], resource)
                self.assertIn("next", body)
                self.assertIn("previous", body)
                self.assertEqual(len(body["results"]), 1)

    def test_delegate_effective_fields_fall_back_to_invoice(self):
        body = self._get("/api/data/delegates/", self.raw).json()
        row = body["results"][0]
        self.assertEqual(row["invoice_number"], "INV-TEST-1")
        self.assertEqual(row["effective_payment_status"], "Paid")

    def test_delegate_override_wins_over_invoice(self):
        d = BookDelegate.objects.first()
        d.delegate_payment_status = "Refunded"
        d.save()
        row = self._get("/api/data/delegates/", self.raw).json()["results"][0]
        self.assertEqual(row["effective_payment_status"], "Refunded")

    def test_cursor_pagination_walks_without_duplicates_or_gaps(self):
        for n in range(2, 8):
            BookEvent.objects.create(
                invoice_number=f"INV-PAGE-{n}", event_code="TESTEV-26",
                payment_status="Pending",
            )
        seen, url = [], "/api/data/bookings/?page_size=2"
        while url:
            body = self._get(url, self.raw).json()
            seen.extend(r["id"] for r in body["results"])
            nxt = body["next"]
            url = nxt[nxt.index("/api/data/"):] if nxt else None
        self.assertEqual(len(seen), len(set(seen)), "cursor pages overlapped")
        self.assertEqual(sorted(seen),
                         sorted(BookEvent.objects.values_list("id", flat=True)))

    def test_successful_auth_records_usage(self):
        self._get("/api/data/events/", self.raw)
        self.key.refresh_from_db()
        self.assertEqual(self.key.usage_count, 1)
        self.assertIsNotNone(self.key.last_used_at)

    def test_event_code_filter(self):
        body = self._get("/api/data/bookings/?event_code=NOPE", self.raw).json()
        self.assertEqual(body["results"], [])

    def test_writes_are_refused(self):
        resp = self.client.post("/api/data/bookings/", **{HEADER: self.raw})
        self.assertEqual(resp.status_code, 405)


class DataApiWiringTests(TestCase):
    def test_authenticator_is_not_global(self):
        globals_ = settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]
        self.assertNotIn(
            "dataapi.authentication.DataApiKeyAuthentication", globals_,
            "DataApiKeyAuthentication must be per-view only.",
        )
        for entry in globals_:
            self.assertNotIn("dataapi", entry)

    def test_session_user_cannot_reach_the_data_api(self):
        """
        A logged-in CRM admin is refused: SessionAuthentication is not in this
        view's authentication_classes, so the session is never consulted and
        request.user stays anonymous. 401, not 403 — DRF answers 401 whenever
        the request carried no credential the view recognises.
        """
        from django.contrib.auth import get_user_model
        User = get_user_model()
        admin = User.objects.create_user(
            username="admin.test", email="a@t.com", password="x", role="admin")
        self.client.force_login(admin)
        self.assertEqual(self.client.get("/api/data/bookings/").status_code, 401)


class CreateDataApiKeyCommandTests(TestCase):
    def test_command_creates_service_user_and_prints_raw_key_once(self):
        from io import StringIO

        from django.contrib.auth import get_user_model
        from django.core.management import call_command

        User = get_user_model()
        out = StringIO()
        call_command("create_data_api_key", "Google Sheets Sync",
                     "--scopes", "bookings,delegates,events", stdout=out)
        output = out.getvalue()

        svc = User.objects.get(username="svc.sheets")
        self.assertFalse(svc.has_usable_password())

        # The service account owns key rows for attribution and nothing else, so
        # it must not be able to act. role="admin" here would be enough on its
        # own, because User.save() grants staff and superuser to that role.
        self.assertEqual(svc.role, "sales")
        self.assertFalse(svc.is_superuser)
        self.assertFalse(svc.is_staff)
        self.assertTrue(svc.is_active)

        key = DataApiKey.objects.get(name="Google Sheets Sync")
        self.assertEqual(key.created_by_id, svc.id)
        self.assertEqual(key.scopes, ["bookings", "delegates", "events"])
        self.assertEqual(key.rate_limit_per_minute, 60)

        # The printed raw key must be the one this row hashes to, and the hash
        # itself must never appear in the output.
        # "..." excludes the abbreviated preview line, which also starts dapi_.
        raw = [t for t in output.split()
               if t.startswith("dapi_") and "..." not in t]
        self.assertEqual(len(raw), 1, output)
        self.assertEqual(DataApiKey.hash_key(raw[0]), key.key_hash)
        self.assertNotIn(key.key_hash, output)

    def test_command_reuses_an_existing_service_user(self):
        from django.core.management import call_command
        from django.contrib.auth import get_user_model

        User = get_user_model()
        call_command("create_data_api_key", "First")
        call_command("create_data_api_key", "Second")
        self.assertEqual(User.objects.filter(username="svc.sheets").count(), 1)
        self.assertEqual(DataApiKey.objects.count(), 2)

    def test_command_demotes_an_already_elevated_service_user(self):
        """
        A svc.sheets left over from the version of this command that created it
        with role="admin" is a superuser. Re-running the command must repair that
        row rather than accept it, which is why the flags are rewritten on every
        run and not only at creation.
        """
        from django.core.management import call_command
        from django.contrib.auth import get_user_model

        User = get_user_model()
        elevated = User.objects.create_user(
            username="svc.sheets", email="svc.sheets@iq-hub.com",
            password="x", role="admin",
        )
        self.assertTrue(elevated.is_superuser)

        call_command("create_data_api_key", "Repair Run")

        elevated.refresh_from_db()
        self.assertEqual(elevated.role, "sales")
        self.assertFalse(elevated.is_superuser)
        self.assertFalse(elevated.is_staff)
        self.assertTrue(elevated.is_active)
