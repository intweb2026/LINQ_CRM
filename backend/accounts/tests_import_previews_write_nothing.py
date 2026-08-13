"""
accounts/tests_import_previews_write_nothing.py
────────────────────────────────────────────────
Every "look before you write" import surface, asserted to actually write nothing.

THE BUG THIS COMES FROM
ticket_central bulk_import's dry_run held its rollback with transaction.savepoint(),
which is a documented NO-OP while the connection is in autocommit. ATOMIC_REQUESTS is
not set on this project, so the connection IS in autocommit: `sid` came back None, the
savepoint_rollback at the end did nothing, and a dry run COMMITTED every row of the
file while the response reported them as `would_insert`. It was found by running one,
and finding two tickets in the database afterwards.

Three surfaces make the same promise, so all three are tested the same way — count
rows, call the endpoint, count again:

    POST /api/tickets/bulk_import/            {"dry_run": true}
    POST /api/paper-reviews/import/preview/
    POST /api/proposal-submissions/import/preview/

The two preview endpoints never wrote — classify_rows() only reads — but "it does not
write" is exactly the property that decays silently the first time someone adds a
convenience save() to a classifier. Untested, it is a comment; tested, it is a rule.

    python manage.py test accounts.tests_import_previews_write_nothing
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from events.models import Event
from paper_review.models import PaperReview
from proposal_submission.models import ProposalSubmission
from ticket_central.models import Ticket

User = get_user_model()

CODE = "PROBE - AA"


class ImportPreviewsWriteNothingTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.hp = User.objects.create_user(
            username="HP", password="x", email="hp@iq-hub.com", role="admin",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.hp)
        # A real event, so rows are otherwise IMPORTABLE. Previewing a file that
        # would fail anyway proves nothing about whether a passing row gets written.
        Event.objects.create(event_code=CODE, official_event_name="Probe Event",
                             event_date="2026-10-01")

    def test_ticket_dry_run_writes_nothing(self):
        rows = [
            {"purpose": "AS", "type_of_ticket": "Blue - BX", "organizer": "Probe A"},
            {"purpose": "AD", "type_of_ticket": "Comp.-CX", "organizer": "Probe B"},
        ]
        before = Ticket.objects.count()
        resp = self.client.post("/api/tickets/bulk_import/", {
            "rows": rows, "duplicate_mode": "allow_all",
            "batch_number": 1, "dry_run": True,
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        # It must still REPORT what it would have done — a dry run that writes
        # nothing and reports nothing is just a broken endpoint.
        self.assertEqual(resp.data["would_insert"], 2)
        self.assertEqual(Ticket.objects.count(), before,
                         "dry_run committed rows it only promised to validate")

    def test_a_real_ticket_import_still_writes(self):
        """The other half: the rollback must not have been left switched on."""
        resp = self.client.post("/api/tickets/bulk_import/", {
            "rows": [{"purpose": "AS", "type_of_ticket": "Blue - BX",
                      "organizer": "Real Import"}],
            "duplicate_mode": "allow_all", "batch_number": 1,
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["inserted"], 1)
        self.assertEqual(Ticket.objects.filter(organizer="Real Import").count(), 1)

    def test_paper_review_preview_writes_nothing(self):
        row = {
            "Event Code": CODE,
            "Speaker Name": "Probe Speaker",
            "Company Name": "Probe Ltd",
            "Email Address of the Speaker": "probe@example.com",
        }
        before = PaperReview.objects.count()
        resp = self.client.post("/api/paper-reviews/import/preview/",
                                {"rows": [row]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["importable"], 1, resp.data["rows"])
        self.assertEqual(PaperReview.objects.count(), before,
                         "preview wrote a paper review")

    def test_proposal_submission_preview_writes_nothing(self):
        row = {
            "Event Code": CODE,
            "Speaker Name": "Probe Speaker",
            "Company Name": "Probe Ltd",
            "Email Address": "probe@example.com",
        }
        before = ProposalSubmission.objects.count()
        resp = self.client.post("/api/proposal-submissions/import/preview/",
                                {"rows": [row]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["importable"], 1, resp.data["rows"])
        self.assertEqual(ProposalSubmission.objects.count(), before,
                         "preview wrote a proposal submission")

    def test_previewing_twice_does_not_accumulate_anything(self):
        """
        A preview is idempotent by construction. Running it repeatedly is how a
        chunked file behaves (8 previews for 3,546 rows), so a per-call write would
        multiply rather than merely appear.
        """
        row = {"Event Code": CODE, "Speaker Name": "Probe", "Company Name": "P",
               "Email Address of the Speaker": "probe@example.com"}
        for _ in range(3):
            self.client.post("/api/paper-reviews/import/preview/",
                             {"rows": [row]}, format="json")
        self.assertEqual(PaperReview.objects.count(), 0)
