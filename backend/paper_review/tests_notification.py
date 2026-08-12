"""
paper_review/tests_notification.py
───────────────────────────────────
PART B — the port of Zoho's `Email_to_Production_Team` (v2).

Everything runs on Django's locmem backend and asserts on mail.outbox; every
address in the fixtures is under example.com / example.invalid, so no test can
reach a real mailbox even if the backend override were lost.

What is pinned: the resolved recipient set, the fallback chain and its separate
alert, the Zoho precedence bug NOT being replicated, refs written as outputs, a
failing send leaving a 201 behind, on_commit not firing on rollback, the MR rule
on internal_footnotes, and a NotificationLog row in every single case.
"""
from datetime import date
from unittest.mock import patch

from django.core import mail
from django.test import override_settings
from rest_framework import serializers as drf_serializers

from paper_review import notifications
from paper_review.models import NotificationLog, PaperReview
from paper_review.notifications import (
    STEP_EVENT_NOT_FOUND, STEP_NO_EVENT_CODE, STEP_NO_SALES_EXECUTIVE,
    resolve_recipients, send_paper_review_notification, subject_for,
)
from paper_review.tests import ALERT, _Base, make_event
from proposal_submission.models import ProposalSubmission


def _review(event_code, **over):
    """A review built straight through the ORM — no serializer, no workflows."""
    fields = {
        "event_code": event_code,
        "paper_submission_date": date(2026, 8, 10),
        "speaker_name": "Eli Jasso",
        "company_name": "Cicada Logistics",
        "email": "eli.jasso@example.com",
        "linkedin_speaker": "https://www.linkedin.com/in/eli-jasso/",
        "linkedin_company": "https://www.linkedin.com/company/cicada/",
        "linkedin_followers": 417,
        "nos": True,
        "closeness_to_topic": 9,
        "closeness_to_region": 2,
        "clear_solution_to_challenges": 9,
        "case_study_results_examples": 1,
        "not_obvious_sales_pitch": 1,
        "company_profile_score": 5,
        "grade": "B",
        "session_location_on_agenda": "Day 1, Afternoon Session",
        "feedback_to_speaker": "Please add a case study.",
        "proposal_received": "Terminal and rail decarbonisation",
        "theme": "terminal and rail environment",
        "agenda_addition": "CHALLENGES IN OILFIELD CULTURE",
    }
    fields.update(over)
    return PaperReview.objects.create(**fields)


class RecipientResolutionTests(_Base):
    """B2 — the two relations that genuinely carry addresses."""

    def test_sales_executive_is_the_to_and_the_two_roles_are_the_cc(self):
        got = resolve_recipients(_review(self.event.event_code))
        self.assertFalse(got.is_fallback)
        self.assertEqual(got.to, ["sales.exec@example.com"])
        self.assertEqual(sorted(got.cc),
                         ["market.research@example.com", "speaker.sales@example.com"])

    def test_assigned_users_outside_the_two_roles_are_not_copied(self):
        # cls.user is assigned to the event with role "sales".
        got = resolve_recipients(_review(self.event.event_code))
        self.assertNotIn("author@example.com", got.to + got.cc)

    def test_the_free_text_event_columns_are_never_consulted(self):
        """
        B2's hard rule: Event.speaker_sales_team / market_research_senior are
        CharField(255) free text, so name-matching them would misroute on a typo.
        Filling them with a real user's NAME must change nothing.
        """
        self.event.speaker_sales_team = "Sam Exec"
        self.event.market_research_senior = "Sam Exec"
        self.event.save()
        got = resolve_recipients(_review(self.event.event_code))
        self.assertEqual(got.to, ["sales.exec@example.com"])
        self.assertEqual(sorted(got.cc),
                         ["market.research@example.com", "speaker.sales@example.com"])

    def test_a_sales_executive_who_is_also_assigned_is_not_duplicated(self):
        self.sales_exec.role = "speaker_sales"
        self.sales_exec.save()
        self.sales_exec.assigned_events.add(self.event)
        got = resolve_recipients(_review(self.event.event_code))
        self.assertEqual(got.to, ["sales.exec@example.com"])
        self.assertNotIn("sales.exec@example.com", got.cc)


class PrecedenceBugTests(_Base):
    """
    B4 — Zoho's `sales_email == "" || sales_email == null && Event_Code != null`
    lets an EMPTY STRING into the block without Event_Code having been checked,
    then traverses a possibly-null lookup. Each case is its own outcome here.
    """

    def test_a_blank_event_code_is_its_own_outcome(self):
        got = resolve_recipients(_review(""))
        self.assertTrue(got.is_fallback)
        self.assertEqual(got.failure_step, STEP_NO_EVENT_CODE)

    def test_an_event_code_with_no_event_is_its_own_outcome(self):
        got = resolve_recipients(_review("GHOST - ZZ"))
        self.assertTrue(got.is_fallback)
        self.assertEqual(got.failure_step, STEP_EVENT_NOT_FOUND)

    def test_an_empty_string_sales_email_does_not_resolve_as_a_recipient(self):
        """The '' case Zoho's precedence let through — guarded explicitly."""
        self.sales_exec.email = ""
        self.sales_exec.save()
        event = make_event("EMPTY - EM")
        event.sales_executive = self.sales_exec
        event.save()
        got = resolve_recipients(_review("EMPTY - EM"))
        self.assertTrue(got.is_fallback)
        self.assertNotIn("", got.to)
        self.assertEqual(got.to, [ALERT])

    def test_a_whitespace_only_address_is_treated_as_absent(self):
        self.sales_exec.email = "   "
        self.sales_exec.save()
        got = resolve_recipients(_review(self.event.event_code))
        # Cc still resolves, so this degrades rather than falling back.
        self.assertFalse(got.is_fallback)
        self.assertEqual(got.failure_step, "sales_executive_has_no_email")
        self.assertEqual(sorted(got.to),
                         ["market.research@example.com", "speaker.sales@example.com"])


class EmailFieldOrderTests(_Base):
    """
    B4 — EMAIL_FIELDS pinned to the Deluge's confirmed actual order. A future
    edit that reorders or drops an entry fails here rather than being noticed
    only by someone reading a production email months later.
    """

    EXPECTED_ORDER = [
        "paper_submission_date", "event_code", "speaker_name", "company_name",
        "email", "linkedin_speaker", "linkedin_followers", "linkedin_company",
        "nos", "proposal_received", "agenda_addition", "theme",
        "proposal_score", "grade", "session_location_on_agenda",
        "internal_footnotes", "feedback_to_speaker",
    ]

    def test_exactly_seventeen_fields_in_the_deluge_order(self):
        fields = [f for f, _ in notifications.EMAIL_FIELDS]
        self.assertEqual(len(fields), 17)
        self.assertEqual(fields, self.EXPECTED_ORDER)

    def test_the_form_derived_labels_are_kept_not_the_shorthand_names(self):
        by_field = dict(notifications.EMAIL_FIELDS)
        self.assertEqual(by_field["email"], "Email address of the speaker")
        self.assertEqual(by_field["session_location_on_agenda"],
                         "Session or location on agenda")
        self.assertEqual(by_field["feedback_to_speaker"],
                         "Feedback to speaker or request information")

    def test_body_renders_fields_in_that_exact_order(self):
        review = _review(self.event.event_code)
        _, html = notifications.render_body(review, include_internal_footnotes=True)
        positions = [html.index(label) for _, label in notifications.EMAIL_FIELDS]
        self.assertEqual(positions, sorted(positions))


class HappyPathTests(_Base):
    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_one_email_with_the_zoho_subject_and_the_resolved_recipients(self):
        r = self.create_review()
        self.assertEqual(r.status_code, 201, r.content)

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.subject, "New Paper Review: AFS - JS - Eli Jasso")
        self.assertEqual(sent.to, ["sales.exec@example.com"])
        self.assertEqual(sorted(sent.cc),
                         ["market.research@example.com", "speaker.sales@example.com"])
        self.assertNotIn(ALERT, sent.to + sent.cc)

    def test_the_body_carries_the_deluge_field_table(self):
        self.create_review()
        html = mail.outbox[0].alternatives[0][0]
        for field, label in notifications.EMAIL_FIELDS:
            if field in notifications.MR_FIELDS:
                continue          # excluded for this mixed recipient list
            with self.subTest(label=label):
                self.assertIn(label, html)
        self.assertIn("Eli Jasso", html)
        self.assertIn("27 / 45", html)

    def test_the_subject_falls_back_to_unknown_event(self):
        self.assertEqual(subject_for(_review("")),
                         "New Paper Review: Unknown Event - Eli Jasso")

    def test_a_notification_log_row_records_the_send(self):
        self.create_review()
        log = NotificationLog.objects.get()
        self.assertEqual(log.status, NotificationLog.Status.RESOLVED)
        self.assertEqual(log.to_addresses, ["sales.exec@example.com"])
        self.assertEqual(sorted(log.cc_addresses),
                         ["market.research@example.com", "speaker.sales@example.com"])
        self.assertEqual(log.error, "")
        self.assertIn("AFS - JS", log.subject)

    def test_the_refs_are_written_from_what_actually_resolved(self):
        """B5 — outputs, not inputs."""
        rid = self.create_review().data["id"]
        review = PaperReview.objects.get(id=rid)
        self.assertEqual(review.speaker_email_ref, "speaker.sales@example.com")
        self.assertEqual(review.research_email_ref, "market.research@example.com")

    def test_the_refs_cannot_be_written_by_the_client(self):
        r = self.create_review(speaker_email_ref="attacker@example.com",
                               research_email_ref="attacker@example.com")
        review = PaperReview.objects.get(id=r.data["id"])
        self.assertNotEqual(review.speaker_email_ref, "attacker@example.com")
        self.assertNotEqual(review.research_email_ref, "attacker@example.com")

    def test_the_review_and_its_proposal_both_exist_alongside_the_email(self):
        self.create_review()
        self.assertEqual(PaperReview.objects.count(), 1)
        self.assertEqual(ProposalSubmission.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)


class FallbackTests(_Base):
    """B3 — the watchdog gets the body AND a separate alert naming the step."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # No sales executive, and nobody assigned in either Cc role.
        cls.orphan = make_event("ORPH - AN", "Orphaned Event")
        cls.assign_events(cls.orphan)

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_two_emails_go_out_and_both_land_on_the_watchdog(self):
        r = self.create_review(event_code="ORPH - AN")
        self.assertEqual(r.status_code, 201, r.content)

        self.assertEqual(len(mail.outbox), 2)
        for sent in mail.outbox:
            self.assertEqual(sent.to, [ALERT])

        subjects = [m.subject for m in mail.outbox]
        self.assertIn("New Paper Review: ORPH - AN - Eli Jasso", subjects)
        self.assertTrue(any("RECIPIENT FALLBACK" in s for s in subjects))

    def test_the_alert_names_the_failed_step_and_carries_the_original_body(self):
        self.create_review(event_code="ORPH - AN")
        alert = next(m for m in mail.outbox if "RECIPIENT FALLBACK" in m.subject)
        self.assertIn(STEP_NO_SALES_EXECUTIVE, alert.body)
        self.assertIn("--- original body ---", alert.body)
        self.assertIn("Eli Jasso", alert.body)

    def test_the_log_records_the_fallback_and_the_reason(self):
        self.create_review(event_code="ORPH - AN")
        log = NotificationLog.objects.get()
        self.assertEqual(log.status, NotificationLog.Status.FALLBACK)
        self.assertEqual(log.to_addresses, [ALERT])
        self.assertIn(STEP_NO_SALES_EXECUTIVE, log.error)

    def test_the_review_and_proposal_are_unaffected(self):
        r = self.create_review(event_code="ORPH - AN")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(PaperReview.objects.filter(id=r.data["id"]).exists())
        self.assertEqual(ProposalSubmission.objects.count(), 1)

    @override_settings(PAPER_REVIEW_ALERT_EMAIL="someone.else@example.invalid")
    def test_the_watchdog_address_is_one_settings_constant(self):
        """B3 — redirecting it must not need a code edit."""
        self.create_review(event_code="ORPH - AN")
        for sent in mail.outbox:
            self.assertEqual(sent.to, ["someone.else@example.invalid"])

    def test_footnotes_never_ride_along_on_a_fallback(self):
        review = _review("ORPH - AN", internal_footnotes="MR: do not send")
        send_paper_review_notification(review)
        for sent in mail.outbox:
            self.assertNotIn("do not send", sent.body)
            self.assertNotIn("do not send", str(getattr(sent, "alternatives", "")))
        self.assertFalse(NotificationLog.objects.get().included_internal_footnotes)


def _fail_the_notification_only(subject, text, html, to, cc=None):
    """
    Stand-in for notifications._send that fails the notification itself but lets
    the watchdog alerts (subject prefixed "[Linq CRM]") succeed, so the SCRIPT
    ERROR path runs to completion rather than being short-circuited by a second
    exception.
    """
    if subject.startswith("[Linq CRM]"):
        return None
    raise OSError("simulated dead SMTP")


class SendFailureTests(_Base):
    """B6 — a broken send never breaks the create."""

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_dead_smtp_still_returns_201_and_keeps_the_record(self):
        with patch("paper_review.notifications._send",
                   side_effect=_fail_the_notification_only):
            r = self.create_review()

        self.assertEqual(r.status_code, 201, r.content)
        review = PaperReview.objects.get(id=r.data["id"])
        self.assertEqual(review.speaker_name, "Eli Jasso")
        self.assertEqual(ProposalSubmission.objects.count(), 1)

    def test_the_failure_is_logged_as_failed_with_the_reason(self):
        with patch("paper_review.notifications._send",
                   side_effect=_fail_the_notification_only):
            self.create_review()

        log = NotificationLog.objects.get()
        self.assertEqual(log.status, NotificationLog.Status.FAILED)
        self.assertIn("simulated dead SMTP", log.error)
        self.assertIn("OSError", log.error)

    def test_a_script_error_alert_is_attempted_with_the_intended_recipient(self):
        sent_alerts = []

        def spy(subject, text, html, to, cc=None):
            if subject.startswith("[Linq CRM]"):
                sent_alerts.append((subject, text, to))
                return
            raise OSError("simulated dead SMTP")

        with patch("paper_review.notifications._send", side_effect=spy):
            self.create_review()

        self.assertEqual(len(sent_alerts), 1)
        subject, body, to = sent_alerts[0]
        self.assertIn("SCRIPT ERROR", subject)
        self.assertEqual(to, [ALERT])
        self.assertIn("simulated dead SMTP", body)
        self.assertIn("sales.exec@example.com", body)

    def test_a_failing_alert_about_a_failing_send_is_swallowed(self):
        with patch("paper_review.notifications._send",
                   side_effect=OSError("everything is down")):
            r = self.create_review()

        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(NotificationLog.objects.count(), 1)
        self.assertEqual(NotificationLog.objects.get().status,
                         NotificationLog.Status.FAILED)

    def test_a_template_error_is_caught_and_logged_like_a_dead_send(self):
        with patch("paper_review.notifications.render_body",
                   side_effect=KeyError("bad label")):
            r = self.create_review()

        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PaperReview.objects.count(), 1)
        log = NotificationLog.objects.get()
        self.assertEqual(log.status, NotificationLog.Status.FAILED)
        self.assertIn("KeyError", log.error)
        # The SCRIPT ERROR alert is the only thing that goes out.
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("SCRIPT ERROR", mail.outbox[0].subject)


class RollbackTests(_Base):
    """
    B9 + A5 — the two halves of the same guarantee: no email for a review that
    does not exist.
    """

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_on_commit_does_not_fire_when_the_transaction_rolls_back(self):
        with patch(
            "proposal_submission.serializers.ProposalSubmissionSerializer"
            ".validate_speaker_name",
            side_effect=drf_serializers.ValidationError("simulated refusal"),
        ):
            r = self.create_review()

        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(PaperReview.objects.count(), 0)
        self.assertEqual(ProposalSubmission.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(NotificationLog.objects.count(), 0)


class InternalFootnotesRuleTests(_Base):
    """
    B8 — internal_footnotes goes out only when EVERY resolved recipient may read
    it. Mixed lists are all-or-nothing: one email, one body.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth import get_user_model
        U = get_user_model()

        # An event whose entire recipient list is Market Research.
        cls.mr_exec = U.objects.create_user(
            username="pr_mr_exec", password="x", role="market_research",
            email="mr.exec@example.com")
        cls.mr_only_event = make_event("MRO - NL", "MR Only Event")
        cls.mr_only_event.sales_executive = cls.mr_exec
        cls.mr_only_event.save()
        cls.mr_cc = U.objects.create_user(
            username="pr_mr_cc", password="x", role="market_research",
            email="mr.cc@example.com")
        cls.mr_cc.assigned_events.set([cls.mr_only_event])
        cls.assign_events(cls.mr_only_event)

    def test_excluded_when_one_recipient_is_not_mr(self):
        review = _review(self.event.event_code,
                         internal_footnotes="MR: weak on region")
        send_paper_review_notification(review)

        sent = mail.outbox[0]
        html = sent.alternatives[0][0]
        self.assertNotIn("weak on region", sent.body)
        self.assertNotIn("weak on region", html)
        # Omitted, not blanked — the label itself is absent.
        self.assertNotIn("Internal footnotes", html)
        self.assertFalse(NotificationLog.objects.get().included_internal_footnotes)

    def test_included_when_every_recipient_is_mr(self):
        review = _review(self.mr_only_event.event_code,
                         internal_footnotes="MR: weak on region")
        send_paper_review_notification(review)

        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["mr.exec@example.com"])
        self.assertEqual(sent.cc, ["mr.cc@example.com"])
        self.assertIn("weak on region", sent.body)
        self.assertIn("Internal footnotes", sent.alternatives[0][0])
        self.assertTrue(NotificationLog.objects.get().included_internal_footnotes)

    def test_the_other_sixteen_fields_go_out_either_way(self):
        review = _review(self.event.event_code, internal_footnotes="hidden")
        send_paper_review_notification(review)
        html = mail.outbox[0].alternatives[0][0]
        labels = [label for field, label in notifications.EMAIL_FIELDS
                  if field not in notifications.MR_FIELDS]
        self.assertEqual(len(labels), 16)
        for label in labels:
            with self.subTest(label=label):
                self.assertIn(label, html)


class NotificationLogAlwaysWrittenTests(_Base):
    """B7/B9 — every outcome leaves a row, so the question is answerable later."""

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_resolved_fallback_and_failed_each_write_exactly_one_row(self):
        orphan = make_event("NOBODY - NN")
        self.assign_events(orphan)

        send_paper_review_notification(_review(self.event.event_code))
        send_paper_review_notification(_review("NOBODY - NN"))
        with patch("paper_review.notifications._send",
                   side_effect=_fail_the_notification_only):
            send_paper_review_notification(_review(self.event.event_code))

        statuses = list(NotificationLog.objects.order_by("id")
                        .values_list("status", flat=True))
        self.assertEqual(statuses, ["resolved", "fallback", "failed"])

    def test_the_log_is_read_only_in_the_admin(self):
        from django.contrib import admin as dj_admin

        from paper_review.models import NotificationLog as Model
        site_admin = dj_admin.site._registry[Model]
        self.assertFalse(site_admin.has_add_permission(None))
        self.assertFalse(site_admin.has_change_permission(None))
        self.assertFalse(site_admin.has_delete_permission(None))

    def test_the_ref_fields_are_absent_from_the_form_ui(self):
        """
        B5. They are outputs; offering them as inputs meant a typed address was
        silently dropped by the read-only serializer. Asserted against the JSX for
        the same reason accounts/tests_pipeline_modules.py checks the module list
        there: a hand-edited frontend file is exactly what drifts back.
        """
        from pathlib import Path

        from django.conf import settings as dj_settings

        form = (Path(dj_settings.BASE_DIR).parent / "frontend" / "src" / "pages"
                / "paperReview" / "PaperReviewFormModal.jsx")
        if not form.exists():
            self.skipTest("frontend not present in this checkout")
        src = form.read_text(encoding="utf-8")
        for field in ("speaker_email_ref", "research_email_ref"):
            with self.subTest(field=field):
                self.assertNotIn(f"set('{field}')", src)
                self.assertNotIn(f"form.{field}", src)

    def test_the_log_survives_deleting_its_review(self):
        rid = self.create_review().data["id"]
        self.assertEqual(NotificationLog.objects.count(), 1)
        self.client.delete(f"{self.LIST}{rid}/")
        log = NotificationLog.objects.get()
        self.assertIsNone(log.paper_review)
        self.assertIn("AFS - JS", log.subject)
