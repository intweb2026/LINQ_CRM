"""
events/tests_bulk_delete.py
────────────────────────────
EventViewSet.bulk_delete — the Events tab's admin-only multi-row delete.

The one thing worth pinning: the gate is IsAdminRole, NOT the events delete
cell of the permission grid. A user granted can_delete on the module can still
remove one event through DELETE /events/{id}/, and must still be refused here —
otherwise the "admins only" the button was asked for is decoration.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import ActionLog
from events.models import Event
from events.views import EventViewSet
from teams.models import Team, TeamPermission

User = get_user_model()

# The @action's own kwargs are passed through, because that is what the router
# does — as_view({"post": "bulk_delete"}) alone leaves the VIEWSET's
# crm_permission("events") in force and never reaches IsAdminRole at all, so
# every assertion below would be answered by the wrong gate.
DELETE = EventViewSet.as_view({"post": "bulk_delete"}, **EventViewSet.bulk_delete.kwargs)


class EventBulkDeleteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="ev_del_admin", password="x", role="admin",
            email="evda@iq-hub.com",
        )

        # Granted delete on the module, and still not an admin. This is the
        # caller the endpoint exists to refuse.
        deleter_team, _ = Team.objects.get_or_create(
            name="ev_del_grid", defaults={"is_all_access": False},
        )
        TeamPermission.objects.update_or_create(
            team=deleter_team, module="events",
            defaults={"can_view": True, "can_create": False,
                      "can_update": False, "can_delete": True},
        )
        cls.deleter = User.objects.create_user(
            username="ev_del_grid_user", password="x", role="sales",
            email="evdg@iq-hub.com",
        )
        cls.deleter.team = deleter_team
        cls.deleter.save()

    def setUp(self):
        self.factory = APIRequestFactory()
        self.events = [
            Event.objects.create(
                event_code=f"DEL{i} - AA", event_date=f"2026-06-0{i + 1}",
                location="Origin City", official_event_name=f"Doomed {i}",
            )
            for i in range(3)
        ]
        self.ids = [e.id for e in self.events]

    def _post(self, body, user=None):
        req = self.factory.post("/bulk_delete/", body, format="json")
        force_authenticate(req, user=user or self.admin)
        resp = DELETE(req)
        resp.render()
        return resp

    def test_admin_deletes_selected_only(self):
        keep = self.ids[2]
        r = self._post({"ids": self.ids[:2]})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["deleted"], 2)
        self.assertEqual(
            list(Event.objects.values_list("id", flat=True)), [keep],
        )
        self.assertTrue(
            ActionLog.objects.filter(action="Bulk deleted 2 events").exists(),
        )

    def test_module_delete_grant_is_not_enough(self):
        r = self._post({"ids": self.ids}, user=self.deleter)
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(Event.objects.count(), 3)

    def test_bad_input_is_rejected(self):
        self.assertEqual(self._post({"ids": []}).status_code, 400)
        self.assertEqual(self._post({"ids": "all"}).status_code, 400)
        self.assertEqual(self._post({"ids": list(range(1001))}).status_code, 400)
        self.assertEqual(Event.objects.count(), 3)

    def test_unknown_ids_are_a_scope_refusal_not_a_partial_wipe(self):
        r = self._post({"ids": [max(self.ids) + 999]})
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(r.data["deleted"], 0)
        self.assertEqual(Event.objects.count(), 3)
