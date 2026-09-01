"""
paper_review/tests_notifications_disabled.py
──────────────────────────────────────────────
B1 — the kill switch, and its DEFAULT state.

EMAIL_BACKEND is live Brevo SMTP with real credentials (config/settings.py) and
the send is synchronous, so PAPER_REVIEW_NOTIFICATIONS_ENABLED defaulting False is
the only thing between an accidental UAT create and a real inbox. This suite
proves both halves: the default really is off, and switching it on and off again
changes behaviour without needing a process restart (it is read from
django.conf.settings at send time, not cached at import).
"""
from django.core import mail
from django.test import TestCase, override_settings

from paper_review.models import NotificationLog
from paper_review.notifications import send_paper_review_notification
from paper_review.tests import ALERT, FIXED_CC, _Base, make_event


class DefaultIsDisabledTests(TestCase):
    def test_the_flag_defaults_to_false(self):
        """
        settings.py's FALLBACK, not this checkout's live value.

        .env is untracked, and a developer testing mail locally turns the flag ON
        there — that is what PAPER_REVIEW_REDIRECT_ALL_EMAIL exists to make safe.
        Asserting the live value would fail on their machine for a reason that has
        nothing to do with the invariant, which is that a deployment setting
        NOTHING still gets no mail.
        """
        from pathlib import Path

        from django.conf import settings
        source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(
            encoding="utf-8")
        self.assertIn('"PAPER_REVIEW_NOTIFICATIONS_ENABLED", "False"', source)
        self.assertIn('"PAPER_REVIEW_REDIRECT_ALL_EMAIL", ""', source)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   DEFAULT_FROM_EMAIL="crm@example.com",
                   PAPER_REVIEW_ALERT_EMAIL=ALERT,
                   PAPER_REVIEW_NOTIFICATIONS_ENABLED=False)
class SuppressedSendTests(_Base):
    """
    _Base itself forces PAPER_REVIEW_NOTIFICATIONS_ENABLED=True (see tests.py) so
    every other suite proves real sending; this class overrides it back to False
    on top, so it inherits the rich event/user fixtures without inheriting the
    override that would defeat the point of this file.
    """

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_nothing_is_sent_on_the_happy_path(self):
        r = self.create_review()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(mail.outbox), 0)

    def test_nothing_is_sent_even_on_a_fallback_outcome(self):
        """
        "send nothing" per B1 means nothing — not "nothing except the fallback
        alert". A UAT tester hitting the fallback path must not leak a
        RECIPIENT FALLBACK email to a real watchdog address either.
        """
        orphan = make_event("SUPP - OR")
        self.assign_events(orphan)
        r = self.create_review(event_code="SUPP - OR")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(mail.outbox), 0)

    def test_nothing_is_sent_even_when_the_send_would_have_failed(self):
        from unittest.mock import patch
        with patch("paper_review.notifications._send",
                   side_effect=OSError("should never be reached")):
            r = self.create_review()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_suppressed_log_row_carries_the_resolved_recipients(self):
        """
        This is the entire point of B1: recipient resolution stays verifiable
        against real Event data with zero mail leaving the building.
        """
        self.create_review()
        log = NotificationLog.objects.get()
        self.assertEqual(log.status, NotificationLog.Status.SUPPRESSED)
        self.assertEqual(log.to_addresses, ["sales.exec@example.com"])
        self.assertEqual(log.cc_addresses, ["author@example.com"] + FIXED_CC)
        self.assertIn("AFS - JS", log.subject)

    # The Cc is real people — the submitting MRE and the standing list — so an
    # event with no sales executive DEGRADES to them rather than falling back.
    # Emptying the standing list AND the submitter's address is what leaves the
    # watchdog as the only remaining recipient, which is the path under test.
    @override_settings(PAPER_REVIEW_CC_EMAILS=[])
    def test_a_suppressed_fallback_still_carries_the_watchdog_address_and_step(self):
        self.user.email = ""
        self.user.save(update_fields=["email"])
        orphan = make_event("SUPP - FB")
        self.assign_events(orphan)
        self.create_review(event_code="SUPP - FB")
        log = NotificationLog.objects.get()
        self.assertEqual(log.status, NotificationLog.Status.SUPPRESSED)
        self.assertEqual(log.to_addresses, [ALERT])
        self.assertIn("no_sales_executive", log.error)

    def test_the_refs_are_not_stamped_when_suppressed(self):
        """
        B5's refs are "what was ACTUALLY resolved AT SEND TIME" — suppressed
        means there was no send, so nothing is stamped.
        """
        from paper_review.models import PaperReview
        rid = self.create_review().data["id"]
        review = PaperReview.objects.get(id=rid)
        self.assertEqual(review.speaker_email_ref, "")
        self.assertEqual(review.research_email_ref, "")

    def test_the_review_and_its_proposal_still_get_created(self):
        """B1 must not touch Part A at all."""
        from proposal_submission.models import ProposalSubmission
        r = self.create_review()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(ProposalSubmission.objects.count(), 1)

    def test_exactly_one_log_row_per_review_while_suppressed(self):
        self.create_review()
        self.create_review()
        self.assertEqual(NotificationLog.objects.count(), 2)
        self.assertTrue(all(
            s == NotificationLog.Status.SUPPRESSED
            for s in NotificationLog.objects.values_list("status", flat=True)))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   DEFAULT_FROM_EMAIL="crm@example.com",
                   PAPER_REVIEW_ALERT_EMAIL=ALERT)
class ToggleWithoutRestartTests(_Base):
    """
    The flag is read live off django.conf.settings inside _notify, not snapshotted
    at import — override_settings patches that same attribute at runtime, so a
    test flipping it mid-suite is the direct proof a real env var flip needs no
    process restart either.
    """

    def test_flipping_the_setting_changes_behaviour_immediately(self):
        review1 = self._plain_review()
        with override_settings(PAPER_REVIEW_NOTIFICATIONS_ENABLED=False):
            send_paper_review_notification(review1)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(NotificationLog.objects.get().status,
                         NotificationLog.Status.SUPPRESSED)

        review2 = self._plain_review()
        with override_settings(PAPER_REVIEW_NOTIFICATIONS_ENABLED=True):
            send_paper_review_notification(review2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            NotificationLog.objects.exclude(pk__in=[]).latest("id").status,
            NotificationLog.Status.RESOLVED)

    def _plain_review(self):
        from datetime import date
        from paper_review.models import PaperReview
        return PaperReview.objects.create(
            event_code=self.event.event_code, speaker_name="Toggle Test",
            email="toggle@example.com", paper_submission_date=date(2026, 8, 1),
        )
