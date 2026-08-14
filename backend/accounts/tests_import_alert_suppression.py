"""
accounts/tests_import_alert_suppression.py
───────────────────────────────────────────
The Smart Import "auto-generated field" alerts must not fire during a bulk load.

WHY THIS FILE EXISTS
Two endpoints send a real email from inside the import request:

    events/bulk_import/    → events/views.py      (auto code / name / date)
    invoices/bulk_import/  → book_event/views.py  (auto invoice number)

Both were unguarded. EMAIL_BACKEND is live Brevo SMTP with real credentials and
IMPORT_ALERT_EMAIL is a real inbox (config/.env), so the send is not theoretical.
The important part is the CARDINALITY: each endpoint alerts once per CALL, and
the browser chunks an import at 500 rows per call, so one load of the Zoho export
delivers one message per chunk that happens to contain a row missing the relevant
field — dozens of emails from a single import, not one.

IMPORT_ALERT_EMAILS_ENABLED (config/settings.py) now gates both, defaulting False.

TEST DESIGN — copied deliberately from paper_review/tests_import.py's
WorkflowSuppressionTests, because the failure mode it guards against is the same:
asserting an empty outbox proves nothing if the send path is simply dead. So every
suppression assertion here is paired with a CONTROL that forces the flag ON and
proves the very same request DOES send. An empty outbox in the suppressed tests is
therefore the flag's doing, not a broken code path.
"""
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase
from teams.models import Team

User = get_user_model()

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
ALERT = "import-watchdog@example.com"

EVENTS_IMPORT = "/api/events/bulk_import/"
INVOICES_IMPORT = "/api/invoices/bulk_import/"


def _all_access_user(username="importer"):
    """
    Both viewsets are guarded by crm_permission(...), which resolves through
    User.team. An all-access role sidesteps per-module naming entirely.
    """

    role, _ = Team.objects.get_or_create(
        name="test-all-access",
        defaults={"is_all_access": True},
    )
    user = User.objects.create_user(
        username=username, password="testpass123", role=User.Role.ADMIN,
        email=f"{username}@example.com",
    )
    user.team = role
    user.save(update_fields=["team"])
    Token.objects.create(user=user)
    return user


class _Base(APITestCase):
    def setUp(self):
        self.user = _all_access_user()
        self.client.force_authenticate(user=self.user)

    # An event row with NO event_code → auto_code=True → alert-eligible.
    @staticmethod
    def event_row(**over):
        row = {"event_code": "", "name": "", "event_date": ""}
        row.update(over)
        return row

    # An invoice row with NO invoice_number → auto_generated_inv=True.
    @staticmethod
    def invoice_row(**over):
        row = {
            "invoice_number": "",
            "event_code": "TESTEV26",
            "event_name": "Test Event",
            "company_name": "Acme Ltd",
            "contact_name": "Ada Lovelace",
            "contact_email": "ada@example.com",
            "invoice_date": "2026-01-15",
        }
        row.update(over)
        return row

    def import_events(self, rows):
        return self.client.post(
            EVENTS_IMPORT,
            {"rows": rows, "duplicate_strategy": "skip", "batch_number": 1},
            format="json",
        )

    def import_invoices(self, rows):
        return self.client.post(
            INVOICES_IMPORT,
            {"rows": rows, "duplicate_strategy": "skip", "batch_number": 1},
            format="json",
        )


# ══ THE DEFAULT ══════════════════════════════════════════════════════════════

class DefaultIsSuppressedTests(APITestCase):
    def test_the_flag_defaults_to_false(self):
        """
        Suppression must be the DEFAULT, not something the caller passes. Nothing
        in the import request body could be relied on to carry it — the Smart
        Import UI uses the same endpoint.
        """
        from django.conf import settings

        self.assertFalse(settings.IMPORT_ALERT_EMAILS_ENABLED)


# ══ CONTROLS — prove the send path is genuinely live ═════════════════════════

@override_settings(EMAIL_BACKEND=LOCMEM, IMPORT_ALERT_EMAIL=ALERT,
                   DEFAULT_FROM_EMAIL="crm@example.com",
                   IMPORT_ALERT_EMAILS_ENABLED=True)
class ControlTests(_Base):
    """
    With the flag forced ON these requests DO send. Without this class, the
    suppression tests below would pass just as happily against a send path that
    had been deleted.
    """

    def test_the_flag_really_is_on_for_this_class(self):
        from django.conf import settings

        self.assertTrue(settings.IMPORT_ALERT_EMAILS_ENABLED)

    def test_an_event_import_with_the_flag_on_does_send(self):
        resp = self.import_events([self.event_row()])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Auto-Generated Fields", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, [ALERT])

    def test_an_invoice_import_with_the_flag_on_does_send(self):
        resp = self.import_invoices([self.invoice_row()])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Without Invoice Number", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, [ALERT])

    def test_the_alert_is_per_call_not_per_import(self):
        """
        The cardinality this whole file exists for. Three chunks of one logical
        import produce THREE emails when the flag is on — which is exactly what a
        chunked load of the real export would have done.
        """
        for _ in range(3):
            self.import_events([self.event_row()])
        self.assertEqual(len(mail.outbox), 3)


# ══ SUPPRESSION AT THE DEFAULT ═══════════════════════════════════════════════

@override_settings(EMAIL_BACKEND=LOCMEM, IMPORT_ALERT_EMAIL=ALERT,
                   DEFAULT_FROM_EMAIL="crm@example.com",
                   IMPORT_ALERT_EMAILS_ENABLED=False)
class SuppressedTests(_Base):
    def test_an_event_import_sends_nothing(self):
        resp = self.import_events([self.event_row()])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(mail.outbox), 0,
                         "a bulk import must never email the import watchdog")

    def test_an_invoice_import_sends_nothing(self):
        resp = self.import_invoices([self.invoice_row()])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(mail.outbox), 0,
                         "a bulk import must never email the import watchdog")

    def test_a_chunked_load_sends_nothing(self):
        """The scenario in miniature: many calls, every one alert-eligible."""
        for i in range(5):
            self.import_events([self.event_row() for _ in range(3)])
            self.import_invoices([self.invoice_row(contact_email=f"p{i}@example.com")])
        self.assertEqual(len(mail.outbox), 0)

    def test_the_rows_are_still_written_while_suppressed(self):
        """
        Suppression must silence the alert and nothing else — the import itself
        still has to work, or these tests would pass on a broken endpoint.
        """
        from book_event.models import BookEvent
        from events.models import Event

        self.import_events([self.event_row()])
        self.import_invoices([self.invoice_row()])
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(BookEvent.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)
