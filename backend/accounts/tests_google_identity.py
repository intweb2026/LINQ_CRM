"""
Tests for accounts.google_identity.

This logic had no coverage at all before now, and it is the only way into the
CRM. Google's verifier is patched, because the point here is the four rules
applied to what Google says, not Google's signature checking.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.google_identity import GoogleIdentityError, user_from_google_credential

User = get_user_model()
VERIFY = "accounts.google_identity.google_id_token.verify_oauth2_token"


@override_settings(GOOGLE_OAUTH_CLIENT_ID="test-client-id",
                   GOOGLE_OAUTH_ALLOWED_DOMAINS=["iq-hub.com"])
class GoogleIdentityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(
            username="gi.tester", email="gi.tester@iq-hub.com",
            role="sales", is_active=True, login_access=True,
        )

    def google_says(self, **claims):
        base = {"email": "gi.tester@iq-hub.com", "email_verified": True}
        base.update(claims)
        return patch(VERIFY, return_value=base)

    def assert_refused(self, status, **claims):
        with self.google_says(**claims):
            with self.assertRaises(GoogleIdentityError) as caught:
                user_from_google_credential("a-credential")
        self.assertEqual(caught.exception.status, status)

    def test_verified_org_email_returns_its_user(self):
        with self.google_says():
            self.assertEqual(user_from_google_credential("a-credential"), self.user)

    def test_email_match_is_case_insensitive(self):
        with self.google_says(email="GI.Tester@IQ-Hub.com"):
            self.assertEqual(user_from_google_credential("a-credential"), self.user)

    def test_blank_credential_is_refused_without_calling_google(self):
        with patch(VERIFY) as verify:
            with self.assertRaises(GoogleIdentityError) as caught:
                user_from_google_credential("   ")
        self.assertEqual(caught.exception.status, 400)
        verify.assert_not_called()

    def test_invalid_credential_is_refused(self):
        with patch(VERIFY, side_effect=ValueError("bad token")):
            with self.assertRaises(GoogleIdentityError) as caught:
                user_from_google_credential("a-credential")
        self.assertEqual(caught.exception.status, 401)

    def test_unverified_email_is_refused(self):
        # Google will hand back an unverified address. Trusting it would let
        # someone claim a colleague's.
        self.assert_refused(401, email_verified=False)

    def test_email_outside_the_allowed_domain_is_refused(self):
        User.objects.create(username="outsider", email="someone@gmail.com",
                            role="sales", is_active=True, login_access=True)
        self.assert_refused(403, email="someone@gmail.com")

    def test_unprovisioned_email_is_refused_and_creates_nobody(self):
        before = User.objects.count()
        self.assert_refused(403, email="ghost@iq-hub.com")
        self.assertEqual(User.objects.count(), before)

    def test_deactivated_user_is_refused(self):
        # status drives is_active through User.save(), so set status.
        self.user.status = User.Status.INACTIVE
        self.user.save(update_fields=["status"])
        self.assert_refused(403)

    def test_user_without_login_access_is_refused(self):
        self.user.login_access = False
        self.user.save(update_fields=["login_access"])
        self.assert_refused(403)

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="")
    def test_unconfigured_server_is_a_500_not_a_silent_pass(self):
        self.assert_refused(500)

    @override_settings(GOOGLE_OAUTH_ALLOWED_DOMAINS=[])
    def test_empty_allow_list_means_no_domain_restriction(self):
        outsider = User.objects.create(
            username="freelance", email="someone@gmail.com",
            role="sales", is_active=True, login_access=True,
        )
        with self.google_says(email="someone@gmail.com"):
            self.assertEqual(user_from_google_credential("a-credential"), outsider)
