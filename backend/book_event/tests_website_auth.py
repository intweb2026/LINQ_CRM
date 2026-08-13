"""
book_event/tests_website_auth.py
─────────────────────────────────
/api/invoices/create_from_website/ authenticates on X-API-KEY and nothing else.

THE HOLE: OriginAuthentication sat ahead of the key check and authenticated any
request whose Origin/Referer matched settings.CORS_ALLOWED_ORIGINS, returning the
same ApiKeyUser sentinel that a real key produces. Since HasApiKey only asks
`isinstance(request.user, ApiKeyUser)`, a bare

    curl -X POST .../create_from_website/ -H "Origin: https://<any-listed-domain>"

was indistinguishable from a correctly keyed call, and every origin added to the
CORS list widened it. The class is gone; these tests keep it gone.

The URL is spelled literally rather than reversed because it is the exact string
handed to integrating websites — if it ever changes, this file should fail.
"""
from django.test import TestCase, override_settings

URL = "/api/invoices/create_from_website/"
LISTED_ORIGIN = "https://listed-site.example.com"

# Deliberately invalid, and that is the assertion. These tests care only about
# whether a request gets PAST authentication, so the cheapest proof of "past" is a
# 400 from the serializer — reached only after the credential was accepted.
EMPTY_BODY = {}


@override_settings(CORS_ALLOWED_ORIGINS=[LISTED_ORIGIN], WEBSITE_API_KEY="website-key")
class WebsiteBookingAuthTests(TestCase):

    def _post(self, **headers):
        return self.client.post(
            URL, data=EMPTY_BODY, content_type="application/json", **headers,
        )

    # ── The hole, held shut ───────────────────────────────────────────────────

    def test_spoofed_origin_on_the_cors_list_is_not_authenticated(self):
        resp = self._post(HTTP_ORIGIN=LISTED_ORIGIN)
        # 401 vs 403 is DRF's call from the first authenticator's challenge
        # header; the requirement is that it is refused, not which of the two.
        self.assertIn(resp.status_code, (401, 403), resp.content)

    def test_spoofed_referer_on_the_cors_list_is_not_authenticated(self):
        resp = self._post(HTTP_REFERER=f"{LISTED_ORIGIN}/booking/confirm")
        self.assertIn(resp.status_code, (401, 403), resp.content)

    def test_no_credentials_at_all_is_refused(self):
        resp = self._post()
        self.assertIn(resp.status_code, (401, 403), resp.content)

    def test_wrong_key_is_refused_even_from_a_listed_origin(self):
        resp = self._post(HTTP_X_API_KEY="not-the-key", HTTP_ORIGIN=LISTED_ORIGIN)
        self.assertIn(resp.status_code, (401, 403), resp.content)

    # ── What must keep working ────────────────────────────────────────────────

    def test_valid_key_reaches_the_serializer(self):
        """
        400, not 401: the credential was accepted and the empty body was then
        rejected on its merits. That boundary is the whole point of the test.
        """
        resp = self._post(HTTP_X_API_KEY="website-key")
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_valid_key_needs_no_origin(self):
        """The normal shape of a real sender: server-to-server, no Origin header."""
        resp = self._post(HTTP_X_API_KEY="website-key")
        self.assertNotIn(resp.status_code, (401, 403), resp.content)

    def test_valid_key_with_an_unlisted_origin_is_accepted(self):
        resp = self._post(HTTP_X_API_KEY="website-key",
                          HTTP_ORIGIN="https://not-on-any-list.example.org")
        self.assertEqual(resp.status_code, 400, resp.content)

    @override_settings(WEBSITE_API_KEY="")
    def test_unconfigured_server_refuses_rather_than_accepting_anything(self):
        resp = self._post(HTTP_X_API_KEY="anything")
        self.assertIn(resp.status_code, (401, 403), resp.content)

    @override_settings(WEBSITE_API_KEY="")
    def test_unconfigured_server_is_not_a_way_in_via_origin(self):
        """
        The dangerous combination: no key configured AND a listed origin. Under
        the old code the origin path did not care whether WEBSITE_API_KEY was set.
        """
        resp = self._post(HTTP_ORIGIN=LISTED_ORIGIN)
        self.assertIn(resp.status_code, (401, 403), resp.content)


class OriginAuthenticationIsGoneTests(TestCase):
    """
    Import-level guard. Someone reinstating the class — or a merge resurrecting
    it — would otherwise reopen the hole quietly, since nothing else in the
    codebase references it by name any more.
    """

    def test_the_class_no_longer_exists(self):
        import book_event.authentication as auth
        self.assertFalse(
            hasattr(auth, "OriginAuthentication"),
            "OriginAuthentication authenticates on a spoofable header — see this "
            "module's docstring before adding it back.",
        )

    def test_the_endpoint_does_not_reference_it(self):
        from book_event.views import BookEventViewSet
        names = [
            c.__name__ for c in
            BookEventViewSet.create_from_website.kwargs["authentication_classes"]
        ]
        self.assertNotIn("OriginAuthentication", names)
        self.assertIn("ApiKeyAuthentication", names)
