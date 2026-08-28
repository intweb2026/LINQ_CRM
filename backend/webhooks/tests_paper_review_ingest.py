"""
webhooks/tests_paper_review_ingest.py
──────────────────────────────────────
POST /api/webhooks/paper-review/ — the same keys as the booking webhook, the
same delivery log, importer semantics for the write.

WHAT MUST NOT MOVE
  * the key is the credential, and a delivery without one is a 401 that still
    leaves a log row;
  * neither workflow fires — no ProposalSubmission, no production-team email —
    because a sender replaying a backlog would otherwise mint one of each per
    row (paper_review/views.py B2);
  * proposal_score and grade are DERIVED on save, so an ingested row carries the
    sum of its criteria whatever the payload claims.
"""
from datetime import date

from django.test import TestCase, override_settings
from django.urls import reverse

from paper_review.models import PaperReview
from proposal_submission.models import ProposalSubmission
from webhooks.models import WebhookApiKey, WebhookLog
from webhooks.tests_event_resolution import make_event

URL = reverse("webhook-ingest-paper-review")


def review(**overrides):
    """One review, deliberately mixing Zoho labels and model field names."""
    body = {
        "Event Code":                        "PRW - PM",
        "Paper Submission Date":             "2026-03-04",
        "Speaker Name":                      "Ada Speaker",
        "company_name":                      "Example Ltd",
        "Email Address of the Speaker":      "ada@example.com",
        "Closeness to Topic (10)":           9,
        "Closeness to Region (5)":           2,
        "Clear Solution to Challenges (10)": 9,
        "Case Study, Results, Examples (5)": 1,
        "Not an obvious 'Sales Pitch' (5)":  1,
        "Company Profile (10)":              5,
    }
    body.update(overrides)
    return body


@override_settings(WEBHOOK_SECRET_KEY="")
class PaperReviewIngestTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        make_event("PRW - PM", web_bookings=False, event_date=date(2026, 3, 20))
        cls.key = WebhookApiKey.objects.create(
            name="Paper Review", api_key=WebhookApiKey.generate_key())

    def post(self, body, key=None):
        return self.client.post(
            URL, body, content_type="application/json",
            HTTP_X_CRM_API_KEY=self.key.api_key if key is None else key,
        )

    def test_missing_key_is_401_and_still_logged(self):
        response = self.client.post(URL, review(), content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(PaperReview.objects.count(), 0)
        self.assertEqual(WebhookLog.objects.filter(http_status=401).count(), 1)

    def test_one_review_is_created_and_scored(self):
        response = self.post(review())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["created"], 1)

        row = PaperReview.objects.get()
        self.assertEqual(row.event_code, "PRW - PM")
        self.assertEqual(row.email, "ada@example.com")
        # 9 + 2 + 9 + 1 + 1 + 5, derived on save rather than taken from the
        # body; 27/45 is 60%, the bottom of the B band.
        self.assertEqual(row.proposal_score, 27)
        self.assertEqual(row.grade, "B")

        log = WebhookLog.objects.get()
        self.assertEqual(log.status, WebhookLog.Status.SUCCESS)
        self.assertEqual(log.db_insert_status, WebhookLog.DbInsertStatus.INSERTED)
        self.assertEqual(log.records_inserted, 1)
        self.assertEqual(log.event_code, "PRW - PM")

    def test_neither_workflow_fires(self):
        from django.core import mail
        self.post(review())
        self.assertEqual(ProposalSubmission.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_batch_reports_per_row_and_writes_only_the_good_ones(self):
        response = self.post({"rows": [
            review(),
            review(**{"Event Code": "NO-SUCH-EVENT",
                      "Email Address of the Speaker": "bad@example.com"}),
        ]})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["created"], 1)
        self.assertEqual(response.data["skipped"], 1)
        self.assertEqual(
            [r["classification"] for r in response.data["rows"]],
            ["CREATE", "ERROR"],
        )
        self.assertEqual(
            list(PaperReview.objects.values_list("email", flat=True)),
            ["ada@example.com"],
        )
        log = WebhookLog.objects.get()
        self.assertEqual(log.db_insert_status, WebhookLog.DbInsertStatus.PARTIAL)

    def test_every_row_rejected_is_a_400(self):
        response = self.post(review(**{"Event Code": "NO-SUCH-EVENT"}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(PaperReview.objects.count(), 0)
        self.assertEqual(WebhookLog.objects.get().status,
                         WebhookLog.Status.FAILED)

    def test_a_redelivery_warns_rather_than_blocking(self):
        self.post(review())
        response = self.post(review())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(PaperReview.objects.count(), 2)
        self.assertIn("already exists", response.data["rows"][0]["warning"])

    def test_unrecognised_keys_are_reported_not_swallowed(self):
        response = self.post(review(**{"Totally Unknown Column": "x"}))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["unrecognised_columns"],
                         ["Totally Unknown Column"])

    def test_empty_body_is_a_400_with_a_usable_message(self):
        response = self.post({})
        self.assertEqual(response.status_code, 400)
        self.assertIn("No paper review data", response.data["detail"])

    def test_liveness_get_writes_no_log(self):
        response = self.client.get(URL, HTTP_X_CRM_API_KEY=self.key.api_key)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Paper review webhook is live", response.data["message"])
        self.assertEqual(WebhookLog.objects.count(), 0)
