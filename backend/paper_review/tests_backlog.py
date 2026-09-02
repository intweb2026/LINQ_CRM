"""
paper_review/tests_backlog.py
──────────────────────────────
The backfill command. What is pinned is everything that stops it mailing the
wrong people, or the right people twice.

The dry run is the default and sends nothing. A review that already has a
delivered notification is skipped, so a repeat run is harmless. The kill switch
is honoured rather than worked around. And the filters actually narrow, because a
--limit that silently did nothing would release the whole backlog.
"""
from datetime import date
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from paper_review.models import NotificationLog, PaperReview
from paper_review.tests import ALERT, FIXED_CC, _Base, make_event


def run(*args):
    out = StringIO()
    call_command("send_paper_review_backlog", *args, stdout=out, stderr=out)
    return out.getvalue()


def _review(event_code, speaker="Backlog Speaker", **over):
    fields = {
        "event_code": event_code,
        "paper_submission_date": date(2026, 8, 10),
        "speaker_name": speaker,
        "email": "backlog@example.com",
        "closeness_to_topic": 9,
        "closeness_to_region": 2,
        "clear_solution_to_challenges": 9,
        "case_study_results_examples": 1,
        "not_obvious_sales_pitch": 1,
        "company_profile_score": 5,
    }
    fields.update(over)
    return PaperReview.objects.create(**fields)


class BacklogCommandTests(_Base):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.a = _review(cls.event.event_code, "Ada Speaker")
        cls.b = _review(cls.event.event_code, "Ben Speaker")

    def test_the_dry_run_is_the_default_and_sends_nothing(self):
        out = run()
        self.assertIn("DRY RUN", out)
        self.assertIn("Ada Speaker", out)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(NotificationLog.objects.count(), 0)

    def test_the_dry_run_names_the_real_recipients(self):
        """The whole point of it: check the list before anything leaves."""
        out = run()
        self.assertIn("sales.exec@example.com", out)
        for address in FIXED_CC:
            with self.subTest(address=address):
                self.assertIn(address, out)

    def test_send_actually_sends_one_per_review(self):
        run("--send", "--delay", "0")
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(NotificationLog.objects.count(), 2)
        self.assertEqual(
            {m.to[0] for m in mail.outbox}, {"sales.exec@example.com"})

    def test_the_send_reports_who_each_one_actually_reached(self):
        """
        The output has to be usable as a checklist: go to this sales executive,
        ask whether it arrived. So every line carries the id, the status, the To
        and the Cc, and the event and speaker underneath to identify it.
        """
        out = run("--send", "--delay", "0", "--limit", "1")
        self.assertIn(f"#{self.a.id}", out)
        self.assertIn("resolved", out)
        self.assertIn("sales.exec@example.com", out)
        self.assertIn("Ada Speaker", out)
        self.assertIn(self.event.event_code, out)

    def test_the_reported_status_comes_from_the_log_not_from_intent(self):
        """
        A send that blew up must be reported as failed, not as delivered to the
        recipients it resolved to. Reporting intent would tell you to go and ask
        someone about an email that was never sent.
        """
        from unittest.mock import patch
        with patch("paper_review.notifications._send",
                   side_effect=OSError("smtp refused")):
            out = run("--send", "--delay", "0", "--limit", "1")

        self.assertIn("failed", out)
        self.assertIn("smtp refused", out)
        # The headline count, not the word "resolved" — the closing legend names
        # every status, so searching for the word finds it either way.
        self.assertIn("0 of 1 reached the mail server", out)

    def test_the_run_ends_with_a_count_by_outcome(self):
        out = run("--send", "--delay", "0")
        self.assertIn("Delivered:", out)
        self.assertIn("reached the mail server", out)

    def test_a_second_run_sends_nothing_because_the_first_is_recorded(self):
        """
        Idempotence, and the reason a half-finished run can just be re-run. The
        log is the record, so nothing extra has to be tracked.
        """
        run("--send", "--delay", "0")
        mail.outbox = []
        out = run("--send", "--delay", "0")
        self.assertIn("backlog is empty", out)
        self.assertEqual(len(mail.outbox), 0)

    def test_include_sent_overrides_that(self):
        run("--send", "--delay", "0")
        mail.outbox = []
        run("--send", "--delay", "0", "--include-sent")
        self.assertEqual(len(mail.outbox), 2)

    def test_a_suppressed_row_is_still_backlog(self):
        """
        Suppressed means the kill switch ate it, so nobody was told. Counting it
        as sent would leave those reviews unannounced forever.
        """
        with override_settings(PAPER_REVIEW_NOTIFICATIONS_ENABLED=False):
            from paper_review.notifications import send_paper_review_notification
            send_paper_review_notification(self.a)
        self.assertEqual(NotificationLog.objects.get().status,
                         NotificationLog.Status.SUPPRESSED)

        out = run()
        self.assertIn("Ada Speaker", out)

    @override_settings(PAPER_REVIEW_NOTIFICATIONS_ENABLED=False)
    def test_it_refuses_to_send_with_the_kill_switch_off(self):
        """
        Otherwise it would walk the whole backlog, log every row as suppressed,
        mail nobody, and report success — and those rows would then look like
        history to the next run.
        """
        with self.assertRaises(CommandError) as caught:
            run("--send")
        self.assertIn("PAPER_REVIEW_NOTIFICATIONS_ENABLED", str(caught.exception))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(NotificationLog.objects.count(), 0)

    @override_settings(PAPER_REVIEW_NOTIFICATIONS_ENABLED=False)
    def test_the_dry_run_still_works_with_the_kill_switch_off(self):
        """Checking the list must not require arming the thing first."""
        self.assertIn("Ada Speaker", run())

    @override_settings(PAPER_REVIEW_REDIRECT_ALL_EMAIL="ops@example.invalid")
    def test_a_live_redirect_is_called_out_loudly(self):
        out = run()
        self.assertIn("ops@example.invalid", out)
        self.assertIn("not to the people named", out)

    def test_limit_narrows_and_says_so(self):
        out = run("--limit", "1")
        self.assertIn("1 review(s) to send, of 2", out)
        self.assertIn("Ada Speaker", out)
        self.assertNotIn("Ben Speaker", out)

    def test_limit_narrows_the_actual_send_too(self):
        run("--send", "--delay", "0", "--limit", "1")
        self.assertEqual(len(mail.outbox), 1)

    def test_ids_selects_exactly_those(self):
        out = run("--ids", str(self.b.id))
        self.assertIn("Ben Speaker", out)
        self.assertNotIn("Ada Speaker", out)

    def test_event_restricts_to_one_code(self):
        other = make_event("BKLG - OT")
        other.sales_executive = self.sales_exec
        other.save()
        _review("BKLG - OT", "Other Speaker")

        out = run("--event", "BKLG - OT")
        self.assertIn("Other Speaker", out)
        self.assertNotIn("Ada Speaker", out)

    def test_a_bad_id_list_is_refused_rather_than_ignored(self):
        with self.assertRaises(CommandError):
            run("--ids", "12,not-a-number")

    def test_a_bad_date_is_refused_rather_than_ignored(self):
        with self.assertRaises(CommandError):
            run("--since", "01-01-2026")

    def test_reviews_that_resolve_to_nobody_are_counted_before_sending(self):
        """
        The dry run has to say how many would reach the watchdog instead of a
        person, because that is the number worth fixing before releasing them.
        """
        orphan = make_event("BKLG - NB")
        self.assign_events(orphan)
        _review("BKLG - NB", "Orphan Speaker")

        with override_settings(PAPER_REVIEW_CC_EMAILS=[]):
            out = run("--event", "BKLG - NB")
        self.assertIn("resolve to nobody", out)
        self.assertIn(ALERT, out)

    def test_one_failing_review_does_not_halt_the_rest(self):
        """
        send_paper_review_notification catches everything by design; the backlog
        must not stop at the first dead recipient.
        """
        from unittest.mock import patch
        calls = []

        def explode_on_the_first(subject, text, html, to, cc=None):
            calls.append(subject)
            if len(calls) == 1:
                raise OSError("smtp is having a moment")

        with patch("paper_review.notifications._send",
                   side_effect=explode_on_the_first):
            run("--send", "--delay", "0")

        statuses = set(NotificationLog.objects.values_list("status", flat=True))
        self.assertEqual(NotificationLog.objects.count(), 2)
        self.assertIn(NotificationLog.Status.FAILED, statuses)
        self.assertIn(NotificationLog.Status.RESOLVED, statuses)
