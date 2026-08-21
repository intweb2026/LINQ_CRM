"""
google_sync/tests_log_delete.py
────────────────────────────────
The sync history's delete button, and the RBAC module behind it.

Google Sync was admin-only until it got its own entry in CRM_MODULES: the page
was gated on "webhooks" in the frontend and IsAdminRole in the backend, so one
cell decided both "may manage sheet pushes" and "may replay webhook deliveries",
and no grant could open it to a non-admin at all. Both of those are behaviour,
so both are pinned here.

WHAT IS ACTUALLY AT RISK
1. `delete` being readable off the module at all — the button is hidden without
   it, and a hidden button is not a permission check.
2. `view` alone being enough to delete. crm_permission maps DELETE to the delete
   cell, but the log viewset was ReadOnlyModelViewSet until this feature: get the
   mixin wiring wrong and DRF answers 405, which passes a "cannot delete" test
   for the wrong reason and would keep passing after the gate broke.
3. The running-sync refusal. /status/ reads the running row to tell the page a
   job is in flight, so deleting it reports a sync as finished while it is still
   writing to the sheet.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from teams.models import Team

from .models import GoogleSheetSyncLog


def _log(status=GoogleSheetSyncLog.Status.FAILED, **kw):
    return GoogleSheetSyncLog.objects.create(
        sync_type=GoogleSheetSyncLog.SyncType.BOOKINGS,
        sheet_name="Bookings",
        status=status,
        **kw,
    )


def _member(username, team):
    user = User.objects.create_user(
        username=username, password="pw", email=f"{username}@iq-hub.com",
    )
    user.team = team
    user.save()
    return user


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


class SyncLogDeleteTests(TestCase):

    def setUp(self):
        self.viewer_team = Team.objects.create(name="gsync_viewers")
        self.viewer_team.permissions.create(
            module="google_sync", can_view=True, can_create=False,
            can_update=False, can_delete=False,
        )
        self.deleter_team = Team.objects.create(name="gsync_deleters")
        self.deleter_team.permissions.create(
            module="google_sync", can_view=True, can_create=False,
            can_update=False, can_delete=True,
        )

        self.viewer = _client(_member("gs_viewer", self.viewer_team))
        self.deleter = _client(_member("gs_deleter", self.deleter_team))

    def test_the_grant_is_what_allows_the_delete(self):
        log = _log()
        resp = self.deleter.delete(f"/api/google-sync/logs/{log.pk}/")

        self.assertEqual(resp.status_code, 204, resp.content)
        self.assertFalse(GoogleSheetSyncLog.objects.filter(pk=log.pk).exists())

    def test_view_alone_does_not_carry_delete(self):
        log = _log()
        resp = self.viewer.delete(f"/api/google-sync/logs/{log.pk}/")

        # 403, not 405. A 405 would mean the route never reached the permission
        # class, which is the same visible outcome for a completely different and
        # far more fragile reason.
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertTrue(GoogleSheetSyncLog.objects.filter(pk=log.pk).exists())

    def test_a_team_without_the_module_cannot_even_read_the_history(self):
        """The module split is what this asserts: no google_sync row, no page."""
        outsider = _client(_member("gs_outsider", Team.objects.create(name="gsync_none")))

        self.assertEqual(outsider.get("/api/google-sync/logs/").status_code, 403)
        self.assertEqual(outsider.get("/api/google-sync/status/").status_code, 403)

    def test_the_log_of_a_running_sync_is_not_deletable(self):
        log = _log(status=GoogleSheetSyncLog.Status.RUNNING)
        resp = self.deleter.delete(f"/api/google-sync/logs/{log.pk}/")

        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertTrue(GoogleSheetSyncLog.objects.filter(pk=log.pk).exists())
        self.assertIn("still running", resp.json()["error"])

    def test_deleting_one_log_leaves_the_rest_of_the_history(self):
        keep, drop = _log(), _log(status=GoogleSheetSyncLog.Status.SUCCESS)

        self.assertEqual(
            self.deleter.delete(f"/api/google-sync/logs/{drop.pk}/").status_code, 204,
        )
        self.assertEqual(
            list(GoogleSheetSyncLog.objects.values_list("pk", flat=True)), [keep.pk],
        )

    def test_reading_the_history_still_works_for_a_view_only_grant(self):
        """The delete gate must not have narrowed the read it hangs off."""
        _log()
        resp = self.viewer.get("/api/google-sync/logs/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(resp.json()["results"]), 1)
