"""
paper_review/tests_public_form.py
──────────────────────────────────
The MRE form link — /api/paper-review-form/config/ and .../submit/.

WHAT MUST NOT MOVE
  * the link is the whole credential, and it opens ONE reviewer's form: the
    config response and the accepted event codes are that reviewer's assigned
    events and nothing wider, even when the reviewer would have full visibility
    inside the CRM;
  * a submission is a FORM submission, so both ADD workflows run — the
    ProposalSubmission is minted and the notification is logged. This is the one
    behaviour that separates this endpoint from /api/webhooks/paper-review/, and
    a regression here is silent: the review still saves;
  * internal_footnotes follows the same MR rule the CRM form follows: config
    says whether this link's reviewer may write it, an MR reviewer's value is
    stored, and a reviewer outside MR is refused rather than silently dropped;
  * the review is attributed to the reviewer the link names, not to nobody.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from events.models import Event
from paper_review.models import NotificationLog, PaperReview
from proposal_submission.models import ProposalSubmission
from webhooks.models import WebhookApiKey

U = get_user_model()
CONFIG = reverse("paper-review-form-config")
SUBMIT = reverse("paper-review-form-submit")

FORM = WebhookApiKey.Target.PAPER_REVIEW_FORM


def make_event(code, name=None):
    return Event.objects.create(
        event_code=code, official_event_name=name or f"Event {code}",
        event_date=date(2026, 5, 1),
    )


def body(**overrides):
    """A complete, valid submission — every REQUIRED_FIELDS entry filled."""
    payload = {
        "event_code": "BIU",
        "paper_submission_date": "2026-03-04",
        "speaker_name": "Ada Speaker",
        "company_name": "Example Ltd",
        "email": "ada@example.com",
        "linkedin_speaker": "https://linkedin.com/in/ada",
        "linkedin_followers": 1200,
        "closeness_to_topic": 9,
        "closeness_to_region": 2,
        "clear_solution_to_challenges": 9,
        "case_study_results_examples": 1,
        "not_obvious_sales_pitch": 1,
        "company_profile_score": 5,
        "session_location_on_agenda": "Day 1, Morning Session",
        "proposal_received": "A session on charging",
        "theme": "Charging",
        "agenda_addition": "Panel two",
    }
    payload.update(overrides)
    return payload


# The static secret is a server-to-server credential for the booking webhook and
# must not open a personal form; blanked here so the fixtures below test the key
# and not it. One test sets it back to prove that.
@override_settings(WEBHOOK_SECRET_KEY="",
                   PAPER_REVIEW_NOTIFICATIONS_ENABLED=False)
class FormLinkBase(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.biu = make_event("BIU", "Charging USA 2026")
        cls.afs = make_event("AFS - JS", "Aviation Fuel Summit 2026")

        cls.mre = U.objects.create_user(
            username="mre_ada", password="x", email="ada.mre@example.com",
            first_name="Ada", last_name="Reviewer", role="market_research")
        cls.mre.assigned_events.set([cls.biu])

        cls.key = WebhookApiKey.objects.create(
            name="Ada — paper review form", api_key=WebhookApiKey.generate_key(),
            target=FORM, mre=cls.mre)

    def config(self, key=None):
        return self.client.get(CONFIG, {"crm_key": key or self.key.api_key})

    def submit(self, payload=None, key=None):
        """
        POST with on_commit callbacks executed, the same way tests.py's
        create_review does. Part B is registered with transaction.on_commit, and
        a TestCase never leaves the outer atomic block, so without this the
        notification half of the workflow silently never runs and the test that
        asserts it would be asserting nothing.
        """
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                f"{SUBMIT}?crm_key={key or self.key.api_key}",
                body() if payload is None else payload,
                content_type="application/json",
            )


class ConfigTests(FormLinkBase):

    def test_config_names_the_reviewer_and_only_their_events(self):
        r = self.config()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["reviewer"], "Ada Reviewer")
        self.assertEqual([e["event_code"] for e in r.data["events"]], ["BIU"])
        self.assertEqual(r.data["rubric_total"], 45)

    def test_config_says_whether_the_reviewer_may_write_footnotes(self):
        self.assertIs(self.config().data["show_internal"], True)
        self.mre.role = "sales"
        self.mre.save(update_fields=["role"])
        self.assertIs(self.config().data["show_internal"], False)

    def test_opening_the_form_counts_as_usage(self):
        self.config()
        self.key.refresh_from_db()
        self.assertEqual(self.key.usage_count, 1)
        self.assertIsNotNone(self.key.last_used_at)

    def test_no_key_is_401(self):
        self.assertEqual(self.client.get(CONFIG).status_code, 401)

    def test_wrong_key_is_401(self):
        self.assertEqual(self.config(key="crm_live_nope").status_code, 401)

    def test_deactivated_link_is_401(self):
        self.key.is_active = False
        self.key.save(update_fields=["is_active"])
        self.assertEqual(self.config().status_code, 401)

    def test_regenerating_kills_the_old_link(self):
        old = self.key.api_key
        self.key.regenerate()
        self.assertEqual(self.config(key=old).status_code, 401)
        self.assertEqual(self.config(key=self.key.api_key).status_code, 200)

    def test_an_ingest_key_cannot_open_a_form(self):
        """A booking key is not a form link, even though it is a valid key."""
        ingest = WebhookApiKey.objects.create(
            name="website-prod", api_key=WebhookApiKey.generate_key())
        self.assertEqual(self.config(key=ingest.api_key).status_code, 401)

    @override_settings(WEBHOOK_SECRET_KEY="legacy-static-secret")
    def test_the_legacy_static_secret_cannot_open_a_form(self):
        r = self.client.get(CONFIG, HTTP_X_WEBHOOK_SECRET="legacy-static-secret")
        self.assertEqual(r.status_code, 401)

    def test_reviewer_with_no_assigned_events_is_refused_not_shown_everything(self):
        self.mre.assigned_events.clear()
        r = self.config()
        self.assertEqual(r.status_code, 409)
        self.assertIn("No events are assigned", r.data["detail"])

    def test_full_visibility_does_not_widen_a_link(self):
        """
        An admin-role reviewer sees the whole catalogue inside the CRM. Their form
        link must not, or the link would publish every event we run.
        """
        self.mre.role = "admin"
        self.mre.save(update_fields=["role"])
        r = self.config()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([e["event_code"] for e in r.data["events"]], ["BIU"])


class SubmitTests(FormLinkBase):

    def test_submission_creates_a_scored_review_attributed_to_the_reviewer(self):
        r = self.submit()
        self.assertEqual(r.status_code, 201, r.content)

        review = PaperReview.objects.get()
        self.assertEqual(review.event_code, "BIU")
        self.assertEqual(review.speaker_name, "Ada Speaker")
        # 9 + 2 + 9 + 1 + 1 + 5 = 27 of 45, which is 60%, the bottom of B.
        self.assertEqual(review.proposal_score, 27)
        self.assertEqual(review.grade, "B")
        self.assertEqual(review.created_by_id, self.mre.id)

    def test_both_add_workflows_run(self):
        """The whole point of not routing this through the webhook."""
        self.assertEqual(self.submit().status_code, 201)
        review = PaperReview.objects.get()

        proposal = ProposalSubmission.objects.get(source_paper_review=review)
        self.assertEqual(proposal.created_by_id, self.mre.id)
        # Notifications are disabled in these settings, so the send is recorded
        # as suppressed rather than skipped — the row proves Part B ran at all.
        self.assertTrue(
            NotificationLog.objects.filter(paper_review=review).exists())

    def test_an_event_outside_the_reviewers_scope_is_refused(self):
        r = self.submit(body(event_code="AFS - JS"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("event_code", r.data)
        self.assertEqual(PaperReview.objects.count(), 0)

    def test_an_unknown_event_is_refused(self):
        r = self.submit(body(event_code="NOPE"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(PaperReview.objects.count(), 0)

    def test_internal_footnotes_is_stored_for_an_mr_reviewer(self):
        r = self.submit(body(internal_footnotes="MR only, do not publish"))
        self.assertEqual(r.status_code, 201, r.content)
        # Stored, but not echoed: the receipt is hand-built and names no MR field.
        self.assertNotIn("internal_footnotes", r.data)
        self.assertEqual(PaperReview.objects.get().internal_footnotes,
                         "MR only, do not publish")

    def test_internal_footnotes_is_refused_for_a_reviewer_outside_mr(self):
        """The form hides the box for them; the serializer refuses it anyway."""
        self.mre.role = "sales"
        self.mre.save(update_fields=["role"])
        r = self.submit(body(internal_footnotes="not mine to write"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("internal_footnotes", r.data)
        self.assertEqual(PaperReview.objects.count(), 0)

    def test_a_missing_required_field_is_a_400_and_writes_nothing(self):
        payload = body()
        del payload["speaker_name"]
        r = self.submit(payload)
        self.assertEqual(r.status_code, 400)
        self.assertIn("speaker_name", r.data)
        self.assertEqual(PaperReview.objects.count(), 0)
        self.assertEqual(ProposalSubmission.objects.count(), 0)

    def test_a_criterion_above_its_maximum_is_refused(self):
        r = self.submit(body(closeness_to_region=99))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(PaperReview.objects.count(), 0)

    def test_no_key_writes_nothing(self):
        r = self.client.post(SUBMIT, body(), content_type="application/json")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(PaperReview.objects.count(), 0)

    def test_the_key_may_also_travel_in_the_header(self):
        r = self.client.post(
            SUBMIT, body(), content_type="application/json",
            HTTP_X_CRM_API_KEY=self.key.api_key)
        self.assertEqual(r.status_code, 201, r.content)


class KeyIssueTests(APITestCase):
    """
    The admin side. A form link without a reviewer would be a public form that
    can name no events and attribute nothing, so the serializer refuses it.
    """

    @classmethod
    def setUpTestData(cls):
        # username="HP", not merely role="admin": WebhookApiKeyViewSet is gated
        # by IsHPAccount, so the keys page is the HP account's alone.
        cls.admin = U.objects.create_user(
            username="HP", password="x", email="a@x.com", role="admin")
        cls.mre = U.objects.create_user(
            username="pr_form_mre", password="x", email="m@x.com",
            role="market_research")

    def setUp(self):
        self.client.force_authenticate(user=self.admin)

    def test_a_form_link_must_name_a_reviewer(self):
        r = self.client.post("/api/webhooks/keys/", {
            "name": "orphan form", "target": FORM, "event": "", "notes": "",
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("mre", r.data)

    def test_a_reviewer_on_a_booking_key_is_refused(self):
        r = self.client.post("/api/webhooks/keys/", {
            "name": "confused key", "target": "bookings", "mre": self.mre.id,
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("mre", r.data)

    def test_a_form_link_is_issued_and_reports_the_submit_path(self):
        r = self.client.post("/api/webhooks/keys/", {
            "name": "Ada form", "target": FORM, "mre": self.mre.id,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)

        key = WebhookApiKey.objects.get(name="Ada form")
        self.assertEqual(key.mre_id, self.mre.id)
        self.assertEqual(key.ingest_path(), SUBMIT)

    def test_patching_a_form_link_cannot_drop_its_reviewer(self):
        key = WebhookApiKey.objects.create(
            name="Ada form", api_key=WebhookApiKey.generate_key(),
            target=FORM, mre=self.mre)
        r = self.client.patch(f"/api/webhooks/keys/{key.id}/",
                              {"mre": None}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("mre", r.data)
