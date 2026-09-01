"""
ticket_central/tests_modified_time.py
──────────────────────────────────────
Modified Time ("updated_at") and Added User ("added_user_text") through the
importer and the create serializer.

WHAT WAS WRONG
_coerce_row read a row's Modified Time and then threw it away, so every imported
ticket showed the moment of the upload; and the upsert branch wrote with
queryset.update(), which never fires auto_now, so re-importing a ticket left
Modified Time reading the date of the PREVIOUS import. Added User was only ever
written by an import, so tickets raised in this CRM showed the column blank.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django.utils.timezone import make_aware

from ticket_central.models import Ticket
from ticket_central.serializers import TicketCreateSerializer
from ticket_central.utils import _coerce_row, import_fields
from ticket_central.views import TicketViewSet

User = get_user_model()

ZOHO_MT = make_aware(datetime(2024, 5, 6, 7, 8, 9))


class ImportModifiedTimeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="importer", password="x", first_name="Ada", last_name="Lovelace",
        )

    def _import(self, rows, mode="allow_all", dedup=None, existing=None):
        request = SimpleNamespace(user=self.user)
        return TicketViewSet()._bulk_import_apply(
            rows, mode, dedup, existing or set(), request,
        )

    # ── coercion ──────────────────────────────────────────────────────────────
    def test_modified_time_survives_coercion(self):
        for key in ("Modified Time", "modified_time", "updated_at"):
            with self.subTest(key=key):
                out = _coerce_row({"purpose": "LX", key: "2024-05-06 07:08:09"})
                self.assertEqual(out["_modified_time"], ZOHO_MT)
                # never as a model field — updated_at is auto_now
                self.assertNotIn("updated_at", out)

    def test_modified_time_is_a_mappable_column(self):
        self.assertIn(("updated_at", "Modified Time"), import_fields())

    # ── insert ────────────────────────────────────────────────────────────────
    def test_insert_keeps_the_files_modified_time(self):
        self._import([{"purpose": "LX", "Modified Time": "2024-05-06 07:08:09"}])
        self.assertEqual(Ticket.objects.get().updated_at, ZOHO_MT)

    def test_insert_without_one_stamps_now(self):
        self._import([{"purpose": "LX"}])
        self.assertLess(
            timezone.now() - Ticket.objects.get().updated_at, timedelta(minutes=5),
        )

    # ── upsert ────────────────────────────────────────────────────────────────
    def _stale_ticket(self):
        t = Ticket.objects.create(external_id="ZHO-1", purpose="LX")
        Ticket.objects.filter(pk=t.pk).update(updated_at=ZOHO_MT)
        return t

    def test_upsert_moves_modified_time_forward(self):
        t = self._stale_ticket()
        _, updated, _, errors = self._import(
            [{"external_id": "ZHO-1", "purpose": "CEU"}],
            mode="upsert_by_external_id", dedup="external_id", existing={"ZHO-1"},
        )
        self.assertEqual((updated, errors), (1, []))
        t.refresh_from_db()
        self.assertEqual(t.purpose, "CEU")
        self.assertGreater(t.updated_at, ZOHO_MT)

    def test_upsert_prefers_the_files_modified_time(self):
        t = self._stale_ticket()
        newer = "2025-01-02 03:04:05"
        self._import(
            [{"external_id": "ZHO-1", "purpose": "CEU", "Modified Time": newer}],
            mode="upsert_by_external_id", dedup="external_id", existing={"ZHO-1"},
        )
        t.refresh_from_db()
        self.assertEqual(t.updated_at, make_aware(datetime(2025, 1, 2, 3, 4, 5)))

    # ── Added User ────────────────────────────────────────────────────────────
    def test_import_stamps_the_importing_user(self):
        self._import([{"purpose": "LX"}])
        self.assertEqual(Ticket.objects.get().added_user_text, "Ada Lovelace")

    def test_import_keeps_the_files_added_user(self):
        self._import([{"purpose": "LX", "added_user_text": "zoho_linq-corporate"}])
        self.assertEqual(
            Ticket.objects.get().added_user_text, "zoho_linq-corporate",
        )

    def test_upsert_does_not_rewrite_added_user(self):
        t = self._stale_ticket()
        Ticket.objects.filter(pk=t.pk).update(added_user_text="zoho_linq-corporate")
        self._import(
            [{"external_id": "ZHO-1", "purpose": "CEU"}],
            mode="upsert_by_external_id", dedup="external_id", existing={"ZHO-1"},
        )
        t.refresh_from_db()
        self.assertEqual(t.added_user_text, "zoho_linq-corporate")

    def test_new_ticket_records_who_raised_it(self):
        ser = TicketCreateSerializer(
            data={"purpose": "LX", "type_of_ticket": "Blue - BX"},
            context={"request": SimpleNamespace(user=self.user)},
        )
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertEqual(ser.save().added_user_text, "Ada Lovelace")
