"""
accounts/tests_event_picker_sources.py
────────────────────────────────────────
A2 — the event picker in BOTH pipeline form modals must read its own module's
permitted_events endpoint, not the full events catalogue.

WHY THIS IS ASSERTED AGAINST THE JSX
The backend half is already covered (PermittedEventsTests in both apps prove the
endpoint returns the right codes and that every offered code creates). What those
tests cannot see is whether the form actually READS it — and for
proposal_submission the endpoint existed, was correct, and was ignored by the
modal for its entire life. That gap is invisible to any backend test, so it is
pinned here the same way accounts/tests_pipeline_modules.py pins the frontend's
copy of CRM_MODULES: a crude read of the file, skipped on a backend-only checkout.

A scoped user offered the whole catalogue gets a 400 on save for every code they
are not assigned to, which reads as a broken module rather than a scoped one.
"""
from pathlib import Path

from django.conf import settings
from django.test import TestCase

FRONTEND = Path(settings.BASE_DIR).parent / "frontend" / "src"

MODALS = [
    ("proposal_submission",
     FRONTEND / "pages" / "proposalSubmission" / "ProposalFormModal.jsx",
     "proposalApi.permittedEvents"),
    ("paper_review",
     FRONTEND / "pages" / "paperReview" / "PaperReviewFormModal.jsx",
     "paperReviewApi.permittedEvents"),
]

API_MODULES = [
    (FRONTEND / "api" / "proposalSubmission.js",
     "proposal-submissions/permitted_events/"),
    (FRONTEND / "api" / "paperReview.js",
     "paper-reviews/permitted_events/"),
]


class EventPickerSourceTests(TestCase):
    def _frontend_present(self):
        if not FRONTEND.exists():
            self.skipTest("frontend/src not present in this checkout")

    def test_both_modals_read_permitted_events(self):
        self._frontend_present()
        for module, path, expected_call in MODALS:
            with self.subTest(module=module):
                self.assertTrue(path.exists(), f"missing {path}")
                src = path.read_text(encoding="utf-8")
                self.assertIn(
                    expected_call, src,
                    f"{path.name} must fetch its own module's permitted_events")

    def test_neither_modal_still_fetches_the_full_catalogue(self):
        """
        The specific regression: `useFetch(eventsApi.list, ...)` offered all 142
        events. Importing api/events at all in these two files is the smell, so
        that is what is checked — a leftover import is how the old call comes back.
        """
        self._frontend_present()
        for module, path, _ in MODALS:
            with self.subTest(module=module):
                src = path.read_text(encoding="utf-8")
                self.assertNotIn(
                    "eventsApi.list", src,
                    f"{path.name} must not offer the whole event catalogue")
                self.assertNotIn(
                    "api/events", src,
                    f"{path.name} should no longer import the events catalogue")

    def test_both_api_modules_expose_permitted_events_against_the_right_path(self):
        self._frontend_present()
        for path, expected_url in API_MODULES:
            with self.subTest(api=path.name):
                self.assertTrue(path.exists(), f"missing {path}")
                src = path.read_text(encoding="utf-8")
                self.assertIn("permittedEvents", src)
                self.assertIn(expected_url, src)

    def test_neither_list_reads_only_page_one(self):
        """
        A3 — the pagination bug, pinned in both api modules. `r.data.results` off a
        single un-paged GET silently returns the first 50 rows and the table reads
        as "there are only 50 records".
        """
        self._frontend_present()
        for path, _ in API_MODULES:
            with self.subTest(api=path.name):
                src = path.read_text(encoding="utf-8")
                self.assertIn("fetchAllPages", src)
                self.assertNotIn("r.data.results || r.data", src)
