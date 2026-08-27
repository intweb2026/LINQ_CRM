"""
book_delegate/tests_delete_permission.py
────────────────────────────────────────
The Bookings delete grant is the gate on delegates/bulk_delete/.

THE REPORT THIS LOCKS DOWN
"I gave the user delete access and the delete button still does nothing."

The endpoint carried permission_classes=[IsAdminRole], which REPLACES the
viewset's crm_permission("bookings") rather than adding to it. IsAdminRole
admits only the HP account, role == "admin", or a team flagged is_all_access —
so ticking Bookings → delete in the permission grid changed nothing here. The
UI shows the Delete button on exactly that cell (BookingsPage.jsx), and the
frontend handled no error, so the button appeared, the request 403'd, and the
rows stayed with nothing said.

Two halves, and both matter:

  * the GRANT is what opens the action — team grid or a per-user override, the
    two routes User.effective_permissions() resolves;
  * the SCOPE still bounds the rows — the grant buys the verb, never reach
    outside the caller's assigned events. accounts/tests_write_scoping.py owns
    the scoping property in general; the last test here pins that this fix did
    not cost it.

    python manage.py test book_delegate.tests_delete_permission
"""
from datetime import date

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import UserPermission
from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event
from teams.models import Team, TeamPermission

User = get_user_model()

IN_SCOPE = "DELPERM - AA"
OUT_OF_SCOPE = "DELPERM - ZZ"


class BulkDeletePermissionTests(APITestCase):
    """role="sales", team NOT is_all_access — the caller IsAdminRole refused."""

    def setUp(self):
        self.team = Team.objects.create(name="Delete Grant Team")
        self.user = User.objects.create_user(
            username="dp_member", password="x", email="dp@iq-hub.com",
            role="sales", team=self.team,
        )
        self.event_in = Event.objects.create(
            event_code=IN_SCOPE, name="In scope", event_date=date(2026, 6, 1))
        self.event_out = Event.objects.create(
            event_code=OUT_OF_SCOPE, name="Out of scope", event_date=date(2026, 6, 2))
        self.user.assigned_events.add(self.event_in)

        self.inv_in = BookEvent.objects.create(
            invoice_number="DP-IN", event_code=IN_SCOPE)
        self.inv_out = BookEvent.objects.create(
            invoice_number="DP-OUT", event_code=OUT_OF_SCOPE)
        self.d_in = BookDelegate.objects.create(
            invoice=self.inv_in, event_code=IN_SCOPE,
            first_name="In", last_name="Scope", email="dp-in@example.com")
        self.d_out = BookDelegate.objects.create(
            invoice=self.inv_out, event_code=OUT_OF_SCOPE,
            first_name="Out", last_name="Scope", email="dp-out@example.com")

        self.client.force_authenticate(self.user)

    def _grid(self, **cells):
        TeamPermission.objects.update_or_create(
            team=self.team, module="bookings", defaults=cells)

    def _delete(self, ids):
        return self.client.post(
            "/api/delegates/bulk_delete/", {"ids": ids}, format="json")

    # ── The grant opens the action ───────────────────────────────────────────

    def test_team_grid_delete_grant_lets_a_non_admin_delete(self):
        """The regression. This was 403 while the button was on screen."""
        self._grid(can_view=True, can_delete=True)

        resp = self._delete([self.d_in.id])

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["deleted"], 1)
        self.assertFalse(BookDelegate.objects.filter(id=self.d_in.id).exists())

    def test_per_user_override_delete_grant_is_enough_on_its_own(self):
        """Singling somebody out is the other half of "I gave them access"."""
        self._grid(can_view=True, can_delete=False)
        UserPermission.objects.create(
            user=self.user, module="bookings", can_delete=True)

        resp = self._delete([self.d_in.id])

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(BookDelegate.objects.filter(id=self.d_in.id).exists())

    # ── ...and only the grant ────────────────────────────────────────────────

    def test_view_without_delete_is_still_refused(self):
        """Reading Bookings must not carry the right to empty it."""
        self._grid(can_view=True, can_delete=False)

        resp = self._delete([self.d_in.id])

        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertTrue(BookDelegate.objects.filter(id=self.d_in.id).exists())

    def test_delete_without_view_is_refused(self):
        """can_view is a prerequisite — a module you cannot open is not one you
        can empty. See crm_permissions.crm_permission."""
        self._grid(can_view=False, can_delete=True)

        resp = self._delete([self.d_in.id])

        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertTrue(BookDelegate.objects.filter(id=self.d_in.id).exists())

    def test_a_per_user_revoke_beats_the_team_grant(self):
        """False is a real value, not "inherit" — it has to take the right away."""
        self._grid(can_view=True, can_delete=True)
        UserPermission.objects.create(
            user=self.user, module="bookings", can_delete=False)

        resp = self._delete([self.d_in.id])

        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertTrue(BookDelegate.objects.filter(id=self.d_in.id).exists())

    # ── The grant buys the verb, not the rows ────────────────────────────────

    def test_the_grant_does_not_widen_scope(self):
        """Holding delete must not reach an event this caller is not assigned."""
        self._grid(can_view=True, can_delete=True)

        resp = self._delete([self.d_out.id])

        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertTrue(
            BookDelegate.objects.filter(id=self.d_out.id).exists(),
            "out-of-scope delegate was DELETED by a caller holding only the grant",
        )
