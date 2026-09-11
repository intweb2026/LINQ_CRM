"""
config/tests_react_shell.py
────────────────────────────
The Cross-Origin-Opener-Policy header on the React shell.

THE BUG THIS PINS. SecurityMiddleware stamps same-origin on every response,
which severs window.opener for cross-origin popups. Google Identity Services
returns its credential by posting a message to the window that opened it, so
under same-origin the sign-in popup completes, goes blank on /gsi/transform,
and nothing happens. Nothing raises, nothing logs, and the login page simply
never signs anybody in.

It is invisible in the usual dev setup, which is what makes a test worth
having: `npm start` serves the build from the Node server, which sets no COOP
header, so the fault appears only when the same page comes from Django.
"""
from django.test import Client, TestCase


class ReactShellCoopTests(TestCase):
    def setUp(self):
        self.client = Client(headers={"host": "localhost"})

    def test_the_shell_allows_popups_to_talk_back(self):
        for path in ["/login", "/pre-event-docs/check-in", "/attendance", "/"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Cross-Origin-Opener-Policy"],
                                 "same-origin-allow-popups")

    def test_the_api_keeps_the_stricter_default(self):
        """
        The relaxation is scoped to pages that host a sign-in button. An API
        response opens no popups, so it keeps what SecurityMiddleware sets.
        """
        response = self.client.get("/api/attendance/qr_email_preview/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Cross-Origin-Opener-Policy"], "same-origin")
