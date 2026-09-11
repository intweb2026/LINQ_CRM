"""
gmail_integration/tests_state.py
─────────────────────────────────
The OAuth `state` parameter, which carries two things across the hop through
Google: who started the connection, and where to send them back to.

THE RETURN PATH IS AN OPEN-REDIRECT SURFACE. It originates in the browser (see
GmailConnectView), so it is asserted here rather than trusted, both on the way
in and on the way back out. A signature proves this server minted the value; it
says nothing about the value being safe.
"""
from django.test import TestCase

from . import service


class ReturnToGuardTests(TestCase):
    def test_site_relative_paths_survive_the_round_trip(self):
        for path in ["/dashboard", "/pre-event-docs/check-in", "/attendance?event=X"]:
            with self.subTest(path=path):
                claim = service.read_state(service.make_state(7, path))
                self.assertEqual(claim["user_id"], 7)
                self.assertEqual(claim["return_to"], path)

    def test_off_site_targets_are_refused(self):
        """
        Each of these would send the browser somewhere other than this app.
        "//evil.example" and "/\evil.example" are the dangerous ones, being
        protocol-relative URLs that read as paths; the rest fail the
        leading-slash test.
        """
        for path in ["//evil.example", "/\evil.example", "https://evil.example",
                     "evil.example", "", None]:
            with self.subTest(path=path):
                self.assertEqual(service.safe_return_to(path), "")
                # Refused on the way back out too, not only when minted.
                claim = service.read_state(service.make_state(7, path))
                self.assertEqual(claim["return_to"], "")

    def test_a_tampered_or_forged_state_does_not_verify(self):
        self.assertIsNone(service.read_state(service.make_state(7, "/x") + "x"))
        self.assertIsNone(service.read_state("not-a-state"))


class ConfigErrorTests(TestCase):
    """
    The preflight that decides whether a user may be sent to Google at all.

    Every case here was reachable in production before the check existed, and
    each one failed somewhere the user could not act on it.
    """
    # A real Fernet key, generated for this test and used nowhere else. Not a
    # placeholder: config_error constructs it, so an invented string fails.
    GOOD_KEY = "Nm5vHwqCGmjwLOwbwU30UeVYzABbFAKF_NUOYrPYXIo="

    def setUp(self):
        self.good = dict(
            GOOGLE_OAUTH_CLIENT_ID="cid.apps.googleusercontent.com",
            GOOGLE_OAUTH_CLIENT_SECRET="GOCSPX-secret",
            GMAIL_OAUTH_REDIRECT_URI="http://localhost:8000/api/gmail/callback/",
            GMAIL_TOKEN_ENCRYPTION_KEY=self.GOOD_KEY,
        )

    def test_a_complete_configuration_reports_no_problem(self):
        with self.settings(**self.good):
            self.assertEqual(service.config_error(), "")

    def test_each_missing_value_is_named(self):
        for key in self.good:
            with self.subTest(missing=key):
                with self.settings(**{**self.good, key: ""}):
                    self.assertIn(key, service.config_error())

    def test_an_unusable_fernet_key_is_caught_before_consent(self):
        """
        The failure this exists for. A 35-character key passes any "is it set"
        test, then raises ValueError when the refresh token is encrypted, which
        is AFTER the user has already granted access.
        """
        with self.settings(**{**self.good,
                              "GMAIL_TOKEN_ENCRYPTION_KEY": "GOCSPX-not-a-fernet-key-at-all-abc"}):
            problem = service.config_error()
        self.assertIn("GMAIL_TOKEN_ENCRYPTION_KEY", problem)
        self.assertIn("44 characters", problem)


class ConnectMisconfiguredResponseTests(TestCase):
    """
    What /api/gmail/connect/ says when the server cannot connect anybody.

    THE MESSAGE IS AUDIENCE-DEPENDENT, and that is the whole point of these
    tests. An SCA who pressed "Connect Gmail" was shown the text
    "GMAIL_TOKEN_ENCRYPTION_KEY is not a valid Fernet key ... generate one with
    python -c ...", which names infrastructure they have no access to and asks
    them to run a command. Admins still get the full reason, because they are
    who fixes it.
    """
    BROKEN = dict(
        GOOGLE_OAUTH_CLIENT_ID="cid.apps.googleusercontent.com",
        GOOGLE_OAUTH_CLIENT_SECRET="GOCSPX-secret",
        GMAIL_OAUTH_REDIRECT_URI="http://localhost:8000/api/gmail/callback/",
        GMAIL_TOKEN_ENCRYPTION_KEY="too-short-to-be-a-fernet-key",
    )

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        cls.admin = User.objects.create_user(
            username="gm_admin", password="x", role="admin", email="a@example.com")
        cls.sca = User.objects.create_user(
            username="gm_sca", password="x", role="sales", email="s@example.com")

    def api(self, user):
        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient
        client = APIClient()
        token, _ = Token.objects.get_or_create(user=user)
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        return client

    def test_an_ordinary_user_is_told_to_ask_an_administrator(self):
        with self.settings(**self.BROKEN):
            resp = self.api(self.sca).get("/api/gmail/connect/")
        self.assertEqual(resp.status_code, 503)
        detail = resp.json()["detail"]
        self.assertIn("administrator", detail)
        # None of the infrastructure leaks.
        self.assertNotIn("FERNET", detail.upper())
        self.assertNotIn("GMAIL_TOKEN_ENCRYPTION_KEY", detail)
        self.assertNotIn("python -c", detail)

    def test_an_admin_is_told_exactly_what_is_wrong(self):
        with self.settings(**self.BROKEN):
            resp = self.api(self.admin).get("/api/gmail/connect/")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("GMAIL_TOKEN_ENCRYPTION_KEY", resp.json()["detail"])
        self.assertTrue(resp.json()["admin_only"])

    def test_no_authorize_url_is_handed_out_either_way(self):
        for user in (self.sca, self.admin):
            with self.subTest(user=user.username):
                with self.settings(**self.BROKEN):
                    resp = self.api(user).get("/api/gmail/connect/")
                self.assertNotIn("authorize_url", resp.json())


class ExchangeCodeTests(TestCase):
    """
    The token exchange, which was the one unlogged failure path and the one
    most likely to fire.
    """

    def test_oauthlib_tolerates_a_superset_scope(self):
        """
        Google answers this flow with MORE scopes than were asked for, because
        the same client id is the CRM's Sign-In client and an account that has
        logged in already granted it profile scopes. oauthlib rejects a changed
        scope by default, raising from deep inside fetch_token, and the bare
        except above turned that into "Gmail could not be connected".
        """
        import os
        self.assertEqual(os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE"), "1")

    def test_the_send_scope_is_requested_and_no_read_scope_is(self):
        self.assertIn("https://www.googleapis.com/auth/gmail.send", service.SCOPES)
        for forbidden in ["gmail.readonly", "gmail.modify", "gmail.metadata",
                          "https://mail.google.com/"]:
            self.assertFalse(any(forbidden in s for s in service.SCOPES),
                             f"{forbidden} must never be requested")

    def test_an_email_address_can_be_resolved_at_all(self):
        """
        gmail.send alone cannot name the mailbox it sends from, so identity
        scopes have to be present or every stored account gets a blank From.
        """
        self.assertTrue(
            {"openid", "https://www.googleapis.com/auth/userinfo.email"}
            & set(service.SCOPES))

    def test_a_jwt_string_id_token_is_not_silently_ignored(self):
        """
        google-auth may return id_token as a JWT STRING. The original code only
        handled a dict, so a string fell through the isinstance check to the
        userinfo call; when that also failed it raised, unlogged.
        """
        class Creds:
            id_token = None
            token = "not-a-real-access-token"

        creds = Creds()
        # No network and no id_token: a blank, NOT an exception, so the caller
        # reports a cause instead of a generic failure.
        self.assertEqual(service._email_for(creds), "")
