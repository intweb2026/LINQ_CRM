"""
dataapi/tests_key_management.py
───────────────────────────────
The HP-only key-management surface at /api/data/keys/.

Two properties are under test. The first is the separation: a dapi_ export key
must not be able to list, mint, or revoke keys. The second is the audience: the
surface answers to ONE named account, so an ordinary admin is denied alongside
sales and anonymous. That second one is the whole
reason these tests exist in this shape — the gate was IsAdminRole, and a test
that only ever logs in as "an admin" passes identically before and after the
narrowing. Every case below is a real request, not a read of the permission
class.
"""
from django.test import TestCase
from dataapi.models import DataApiKey
from accounts.models import User
from accounts.permissions import dapi_USERNAME


class KeyMgmtSmoke(TestCase):
    def setUp(self):
        # The owner account is identified by USERNAME, so it is created from the
        # same constant the permission reads. Hardcoding "HP" here would let the
        # two drift and turn a real regression into a still-green test.
        self.hp = User.objects.create_user(
            username=dapi_USERNAME, password="x", role="admin")
        self.admin = User.objects.create_user(username="adm", password="x", role="admin")
        self.sales = User.objects.create_user(username="sls", password="x", role="sales")

    def test_flow(self):
        self.client.force_login(self.hp)
        # create
        r = self.client.post("/api/data/keys/", {
            "name": "Sheets", "scopes": ["tickets", "bookings"]},
            content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        raw = body["raw_key"]
        self.assertTrue(raw.startswith("dapi_"))
        self.assertEqual(body["scopes"], ["bookings", "tickets"])
        self.assertNotIn("key_hash", body)
        # list never leaks the hash
        rows = self.client.get("/api/data/keys/").json()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("key_hash", rows[0])
        self.assertNotIn(raw, str(rows[0]))
        self.assertEqual(rows[0]["created_by"], str(self.hp))
        self.assertTrue(rows[0]["is_active"])
        # the minted key actually authenticates, and only in scope
        h = {"HTTP_X_DATA_API_KEY": raw}
        self.assertEqual(self.client.get("/api/data/tickets/", **h).status_code, 200)
        self.assertEqual(self.client.get("/api/data/events/", **h).status_code, 403)
        # a dapi_ key cannot reach key management. FRESH client: self.client
        # holds the admin session, which would authenticate it regardless.
        from django.test import Client
        self.assertIn(Client().get("/api/data/keys/", **h).status_code, (401, 403))
        # revoke
        kid = body["id"]
        self.assertEqual(self.client.post(f"/api/data/keys/{kid}/revoke/").status_code, 200)
        self.assertFalse(DataApiKey.objects.get(pk=kid).is_active)
        self.assertEqual(self.client.get("/api/data/tickets/", **h).status_code, 401)
        self.assertEqual(len(self.client.get("/api/data/keys/").json()), 1)
        self.assertEqual(self.client.post("/api/data/keys/9999/revoke/").status_code, 404)
        # validation
        bad = self.client.post("/api/data/keys/", {"name": "n", "scopes": ["nope"]},
                               content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("nope", str(bad.json()))
        empty = self.client.post("/api/data/keys/", {"name": "n", "scopes": []},
                                 content_type="application/json")
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(DataApiKey.objects.count(), 1)

    def test_everyone_but_hp_is_denied(self):
        """Anonymous, sales, and a plain admin — every non-owner, on every verb.

        The admin case is the one that matters: an admin is exactly the caller the
        old IsAdminRole gate let in, and listing is included deliberately because
        the list is not a lesser permission here. It names every live credential
        and the scopes it holds, which is most of what an attacker would want.
        """
        key = DataApiKey.create_key(name="existing", scopes=["events"], created_by=self.hp)[0]

        # anonymous is 401 from TokenAuthentication, not 403
        self.assertEqual(self.client.get("/api/data/keys/").status_code, 401)

        for user in (self.sales, self.admin):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get("/api/data/keys/").status_code, 403)
                self.assertEqual(
                    self.client.post("/api/data/keys/", {"name": "x", "scopes": ["events"]},
                                     content_type="application/json").status_code, 403)
                self.assertEqual(
                    self.client.post(f"/api/data/keys/{key.id}/revoke/").status_code, 403)

        # Nothing was minted and nothing was revoked by any of the above.
        self.assertEqual(DataApiKey.objects.count(), 1)
        self.assertTrue(DataApiKey.objects.get(pk=key.id).is_active)
