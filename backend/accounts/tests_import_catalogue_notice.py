"""
accounts/tests_import_catalogue_notice.py
──────────────────────────────────────────
An import preview must say WHY nothing can be imported when the reason is the
system rather than the file.

THE INCIDENT THIS COMES FROM
The Events catalogue was cleared. Both importers resolve every row's Event Code
against it, so the next import returned one ERROR per row — all of them "no
matching event; prefilter candidates []" — the Import button stayed disabled
because nothing was importable, and nothing anywhere said the catalogue was empty.
The reasonable conclusion from the outside was "the import button is broken".

Asserted for BOTH modules: they share accounts/import_common.catalogue_notice, and
the failure mode is identical in each.

    python manage.py test accounts.tests_import_catalogue_notice
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from rest_framework.test import APIClient

from events.models import Event
from paper_review.models import PaperReview
from proposal_submission.models import ProposalSubmission

User = get_user_model()

CODE = "NOTICE - AA"

# Each module names its email column differently, mirroring its own Zoho form:
# "Email Address of the Speaker" for paper review, "Email Address" for proposals.
PR_ROW = {
    "Event Code": CODE,
    "Speaker Name": "Notice Speaker",
    "Company Name": "Notice Ltd",
    "Email Address of the Speaker": "notice@example.com",
}
PS_ROW = {
    "Event Code": CODE,
    "Speaker Name": "Notice Speaker",
    "Company Name": "Notice Ltd",
    "Email Address": "notice@example.com",
}

MODULES = [
    ("paper_review", "/api/paper-reviews/import/", PR_ROW, PaperReview),
    ("proposal_submission", "/api/proposal-submissions/import/", PS_ROW, ProposalSubmission),
]


class CatalogueNoticeTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.hp = User.objects.create_user(
            username="HP", password="x", email="hp@iq-hub.com", role="admin",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.hp)

    def preview(self, base, row):
        resp = self.client.post(base + "preview/", {"rows": [row]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.data

    def test_an_empty_catalogue_is_named_as_the_reason(self):
        self.assertFalse(Event.objects.exists())
        for module, base, row, _ in MODULES:
            with self.subTest(module=module):
                data = self.preview(base, row)
                self.assertEqual(data["importable"], 0)
                self.assertIn("Events catalogue is empty", data["notice"] or "")

    def test_no_notice_once_the_catalogue_has_events(self):
        """
        The notice must not become background noise: it describes one specific
        broken state, so it has to disappear the moment that state is fixed — even
        when a row still fails for its own reasons.
        """
        Event.objects.create(event_code="SOMETHING - ELSE",
                             official_event_name="Other", event_date="2026-01-01")
        for module, base, row, _ in MODULES:
            with self.subTest(module=module):
                data = self.preview(base, row)
                self.assertIsNone(data["notice"])
                # The row still errors — its own code does not resolve — which is
                # exactly the per-row error the notice is NOT about.
                self.assertEqual(data["importable"], 0)

    def test_the_row_imports_once_its_event_exists(self):
        """
        The other half of the diagnosis: nothing is wrong with the importers. With
        the catalogue populated, the same row that produced only errors goes in.
        """
        Event.objects.create(event_code=CODE, official_event_name="Notice Event",
                             event_date="2026-09-01")
        for module, base, row, model in MODULES:
            with self.subTest(module=module):
                data = self.preview(base, row)
                self.assertIsNone(data["notice"])
                self.assertEqual(data["importable"], 1, data["rows"])

                resp = self.client.post(base + "commit/", {
                    "rows": [row],
                    "plan_hash": data["plan_hash"],
                    "import_batch_id": data["import_batch_id"],
                    "filename": "notice.csv",
                }, format="json")
                self.assertEqual(resp.status_code, 201, resp.content)
                self.assertEqual(resp.data["created"], 1)
                self.assertEqual(model.objects.count(), 1)
