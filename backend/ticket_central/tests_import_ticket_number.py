"""
ticket_central/tests_import_ticket_number.py
────────────────────────────────────────────
bulk_import bypasses TicketCreateSerializer, so rows used to insert with
ticket_number="" and stay unnamed until the nightly backfill ran. These prove
the importer now numbers them on arrival, without touching numbers a file
supplies itself.
"""
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from ticket_central.models import Ticket, TicketSequence
from ticket_central.views import TicketViewSet

User = get_user_model()


class ImportTicketNumberTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="importer", password="x")

    def _import(self, rows):
        request = SimpleNamespace(user=self.user)
        return TicketViewSet()._bulk_import_apply(
            rows, "allow_all", None, set(), request,
        )

    def test_missing_number_is_generated(self):
        inserted, _, _, errors = self._import(
            [{"purpose": "LX", "type_of_ticket": "Blue - BX"}]
        )
        self.assertEqual((inserted, errors), (1, []))
        self.assertEqual(Ticket.objects.get().ticket_number, "BX-LX 10001")
        self.assertEqual(
            TicketSequence.objects.get(purpose_key="LX").last_number, 10001
        )

    def test_supplied_number_is_kept(self):
        self._import([{"purpose": "LX", "ticket_number": "LX 9999"}])
        self.assertEqual(Ticket.objects.get().ticket_number, "LX 9999")

    def test_rows_in_one_batch_do_not_collide(self):
        self._import([{"purpose": "LX"}, {"purpose": "LX"}, {"purpose": "ZID"}])
        self.assertEqual(
            sorted(Ticket.objects.values_list("ticket_number", flat=True)),
            ["LX 10001", "LX 10002", "ZID 10001"],
        )

    def test_no_purpose_stays_blank(self):
        inserted, _, _, errors = self._import([{"event_name": "orphan"}])
        self.assertEqual((inserted, errors), (1, []))
        self.assertEqual(Ticket.objects.get().ticket_number, "")
