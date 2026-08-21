"""
webhooks/tests_key_admin_gate.py
────────────────────────────────
Who may reach /api/webhooks/keys/.

These are the website's INGEST credentials, and the property under test is that
their audience is one named account rather than a role. The gate was IsAdminRole,
which admitted every admin and every is_all_access team; a test that logs in as
"an admin" and expects 200 therefore passes identically before and after the
narrowing, which is exactly why the admin denial below is spelled out.

Listing is checked as carefully as writing. WebhookApiKeySerializer serves
`api_key` in the clear, so a caller who can list can post bookings into the CRM —
read is not the lesser permission here.

The delivery logs beside them are NOT part of this. They are operational data on
the same page, they stay on IsAdminRole, and the last test says so, so that
tightening the keys cannot quietly take the logs with it.
"""
from django.test import TestCase

from accounts.models import User
from accounts.permissions import dapi_USERNAME
from webhooks.models import WebhookApiKey


class WebhookKeyGateTests(TestCase):
    def setUp(self):
        # Built from the constant the permission reads, so the two cannot drift.
        self.hp = User.objects.create_user(
            username=dapi_USERNAME, password="x", role="admin")
        self.admin = User.objects.create_user(username="adm", password="x", role="admin")
        self.sales = User.objects.create_user(username="sls", password="x", role="sales")
        self.key = WebhookApiKey.objects.create(
            name="website-prod", api_key=WebhookApiKey.generate_key(), created_by=self.hp)

    def test_hp_can_list_create_regenerate_and_toggle(self):
        self.client.force_login(self.hp)

        rows = self.client.get("/api/webhooks/keys/").json()
        self.assertEqual(rows["count"] if isinstance(rows, dict) else len(rows), 1)

        self.assertEqual(
            self.client.post("/api/webhooks/keys/", {"name": "second", "event": ""},
                             content_type="application/json").status_code, 201)

        before = self.key.api_key
        r = self.client.post(f"/api/webhooks/keys/{self.key.id}/regenerate/")
        self.assertEqual(r.status_code, 200)
        self.key.refresh_from_db()
        self.assertNotEqual(self.key.api_key, before)

        self.assertEqual(
            self.client.post(f"/api/webhooks/keys/{self.key.id}/toggle/").status_code, 200)
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)

    def test_everyone_but_hp_is_denied(self):
        # anonymous is 401 from TokenAuthentication, not 403
        self.assertEqual(self.client.get("/api/webhooks/keys/").status_code, 401)

        for user in (self.sales, self.admin):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get("/api/webhooks/keys/").status_code, 403)
                self.assertEqual(
                    self.client.post("/api/webhooks/keys/", {"name": "x", "event": ""},
                                     content_type="application/json").status_code, 403)
                self.assertEqual(
                    self.client.post(
                        f"/api/webhooks/keys/{self.key.id}/regenerate/").status_code, 403)
                self.assertEqual(
                    self.client.post(
                        f"/api/webhooks/keys/{self.key.id}/toggle/").status_code, 403)

        # Nothing was minted, rotated or disabled by any of the above.
        self.assertEqual(WebhookApiKey.objects.count(), 1)
        row = WebhookApiKey.objects.get(pk=self.key.id)
        self.assertEqual(row.api_key, self.key.api_key)
        self.assertTrue(row.is_active)

    def test_the_delivery_logs_are_not_swept_up_with_the_keys(self):
        """An admin keeps the logs. Only the credential surface narrowed."""
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/api/webhooks/logs/").status_code, 200)
