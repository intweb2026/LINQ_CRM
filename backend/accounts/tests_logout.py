"""
accounts/tests_logout.py
────────────────────────
Signing out must revoke the token, not merely forget it.

DRF tokens have no expiry, so "logout" that only clears localStorage leaves a
working credential behind indefinitely. The six-hour inactivity logout in
frontend/src/components/IdleLogout.jsx is worth nothing without this, since the
person it protects against is precisely the one sitting at the abandoned desk.

    python manage.py test accounts.tests_logout
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

User = get_user_model()


class LogoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="idler", email="idler@iq-hub.com", password="x",
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_logout_deletes_the_token(self):
        res = self.client.post(reverse("logout"))
        self.assertEqual(res.status_code, 204)
        self.assertFalse(Token.objects.filter(user=self.user).exists())

    def test_the_revoked_token_no_longer_authenticates(self):
        """The point of the whole endpoint: the old key must stop working."""
        self.client.post(reverse("logout"))
        res = self.client.get(reverse("users-my-permissions"))
        self.assertEqual(res.status_code, 401)

    def test_a_second_logout_is_not_an_error_for_a_still_valid_session(self):
        """
        Two tabs signing out at once, or the inactivity timer racing the Topbar
        button. The second call arrives with a token that is already gone, so it
        is unauthenticated — 401, never a 500.
        """
        self.client.post(reverse("logout"))
        res = self.client.post(reverse("logout"))
        self.assertEqual(res.status_code, 401)

    def test_logout_flushes_a_django_session_in_the_same_browser(self):
        """
        The other half of "invalidate the session". SessionAuthentication is
        enabled API-wide, so an admin who has also signed into /admin/ holds a
        cookie that keeps working after their token is gone.
        """
        self.client.login(username="idler", password="x")
        self.assertIn("_auth_user_id", self.client.session)
        self.client.post(reverse("logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_a_credential(self):
        anon = APIClient()
        self.assertEqual(anon.post(reverse("logout")).status_code, 401)

    def test_one_users_logout_leaves_other_sessions_alone(self):
        """Nobody else is signed out. The 'do not affect active users' half."""
        other = User.objects.create_user(
            username="worker", email="worker@iq-hub.com", password="x",
        )
        other_token = Token.objects.create(user=other)
        self.client.post(reverse("logout"))
        self.assertTrue(Token.objects.filter(user=other).exists())
        working = APIClient()
        working.credentials(HTTP_AUTHORIZATION=f"Token {other_token.key}")
        self.assertEqual(
            working.get(reverse("users-my-permissions")).status_code, 200,
        )
