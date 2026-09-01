"""
paper_review/tests_paper_to_proposal.py
────────────────────────────────────────
PART A — the port of Zoho's `Paper_to_Proposal_Submiss`.

Every claim the implementation makes is pinned here: the 13-field mapping and the
8 fields left blank, the provenance FK, the stale-score flag, going through the
proposal's own validation instead of Zoho's `insert into`, atomicity in both
directions, the column-width audit, one-proposal-per-review, immediate visibility
to the author, and the duplicate warning that must never block.
"""
from datetime import date
from unittest.mock import patch

from django.db import IntegrityError, transaction
from rest_framework import serializers as drf_serializers

from accounts.models import ActionLog
from paper_review.access import permitted_event_codes
from paper_review.models import PaperReview
from paper_review.proposal_bridge import (
    FIELD_MAP, LEFT_BLANK, create_proposal_for_review, narrower_targets,
)
from paper_review.tests import _Base, make_event
from proposal_submission.models import ProposalSubmission
from proposal_submission.views import BUSINESS_FIELDS


class MappingTests(_Base):
    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_one_proposal_is_created_with_every_mapped_field(self):
        r = self.create_review()
        self.assertEqual(r.status_code, 201, r.content)
        review = PaperReview.objects.get(id=r.data["id"])

        self.assertEqual(ProposalSubmission.objects.count(), 1)
        proposal = ProposalSubmission.objects.get()

        for target, source in FIELD_MAP:
            with self.subTest(field=f"{target} <- {source}"):
                self.assertEqual(getattr(proposal, target), getattr(review, source))

        # Spot-check the renamed pairs by value, not just by equality of getattrs.
        self.assertEqual(proposal.submission_date, date(2026, 8, 10))
        self.assertEqual(proposal.qc_score, 27)
        self.assertEqual(proposal.qc_grade, "B")
        self.assertEqual(proposal.agenda_slot, "Day 1, Afternoon Session")
        self.assertEqual(proposal.presentation_theme, "terminal and rail environment")
        self.assertEqual(proposal.created_by, self.user)

    def test_the_eight_zoho_blanks_stay_blank(self):
        self.create_review()
        proposal = ProposalSubmission.objects.get()
        for field in LEFT_BLANK:
            with self.subTest(field=field):
                self.assertEqual(getattr(proposal, field), "")

    def test_linkedin_company_is_mapped_although_zoho_omits_it(self):
        """The documented divergence — Zoho's omission is an oversight, not a rule."""
        self.create_review()
        proposal = ProposalSubmission.objects.get()
        self.assertEqual(
            proposal.linkedin_company,
            "https://www.linkedin.com/company/cicada-logistics/")

    def test_internal_footnotes_is_not_carried_into_the_mr_block(self):
        review = PaperReview.objects.create(
            event_code=self.event.event_code, speaker_name="Fn Speaker",
            email="fn@example.com", paper_submission_date=date(2026, 8, 1),
            internal_footnotes="MR only",
        )
        request = _request_for(self.user)
        with transaction.atomic():
            proposal, created, _ = create_proposal_for_review(review, request)
        self.assertTrue(created)
        self.assertEqual(proposal.internal_footnotes_mr, "")
        self.assertEqual(proposal.slot_recommendation_mr, "")

    def test_the_mapping_accounts_for_every_business_field(self):
        """
        Drift guard. A new ProposalSubmission column must be a deliberate choice
        here — mapped or explicitly left blank — not silently unmapped.
        """
        covered = {t for t, _ in FIELD_MAP} | set(LEFT_BLANK)
        self.assertEqual(covered, set(BUSINESS_FIELDS))


class ColumnWidthTests(_Base):
    """
    A6 — verified before writing, and kept verified.

    A narrower destination would truncate a legal value, and the three pairs the
    spec calls out are asserted individually so a future migration that narrows one
    of them fails here instead of in the data.
    """

    def test_no_mapped_target_is_narrower_than_its_source(self):
        self.assertEqual(narrower_targets(), [])

    def test_the_three_named_pairs(self):
        pm = ProposalSubmission._meta.get_field
        pr = PaperReview._meta.get_field
        self.assertGreaterEqual(pm("agenda_slot").max_length,
                                pr("session_location_on_agenda").max_length)
        self.assertGreaterEqual(pm("qc_grade").max_length, pr("grade").max_length)
        self.assertEqual(pm("presentation_theme").max_length,
                         pr("theme").max_length)


class ProvenanceTests(_Base):
    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_source_paper_review_points_back_at_the_review(self):
        rid = self.create_review().data["id"]
        proposal = ProposalSubmission.objects.get()
        self.assertEqual(proposal.source_paper_review_id, rid)

    def test_a_manually_created_proposal_has_no_source(self):
        r = self.client.post(self.PROPOSALS, {
            "event_code": self.event.event_code, "speaker_name": "Manual",
            "email": "manual@example.com", "submission_date": "2026-08-01",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(
            ProposalSubmission.objects.get(id=r.data["id"]).source_paper_review)

    def test_source_paper_review_is_read_only_on_the_serializer(self):
        other = PaperReview.objects.create(
            event_code=self.event.event_code, speaker_name="Other",
            email="other@example.com", paper_submission_date=date(2026, 8, 2),
        )
        self.create_review()
        proposal = ProposalSubmission.objects.get()
        r = self.client.patch(f"{self.PROPOSALS}{proposal.id}/",
                              {"source_paper_review": other.id}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        proposal.refresh_from_db()
        self.assertNotEqual(proposal.source_paper_review_id, other.id)

    def test_the_link_is_exposed_on_the_proposal_payload(self):
        rid = self.create_review().data["id"]
        row = self.client.get(self.PROPOSALS).data["results"][0]
        self.assertEqual(row["source_paper_review"], rid)

    def test_deleting_the_review_keeps_the_proposal(self):
        rid = self.create_review().data["id"]
        self.client.delete(f"{self.LIST}{rid}/")
        proposal = ProposalSubmission.objects.get()
        self.assertIsNone(proposal.source_paper_review)


class StaleScoreTests(_Base):
    """
    A3 — neither workflow propagates edits, so re-scoring a review leaves its
    proposal behind. The flag makes that visible instead of silent.
    """

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def _row(self, proposal_id):
        r = self.client.get(f"{self.PROPOSALS}{proposal_id}/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def test_a_freshly_generated_proposal_is_not_stale(self):
        self.create_review()
        proposal = ProposalSubmission.objects.get()
        self.assertFalse(self._row(proposal.id)["qc_score_stale"])

    def test_rescoring_the_review_marks_the_proposal_stale(self):
        rid = self.create_review().data["id"]
        proposal = ProposalSubmission.objects.get()

        r = self.client.patch(f"{self.LIST}{rid}/",
                              {"closeness_to_region": 5}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        # The review recomputed; the proposal deliberately did not follow.
        self.assertEqual(PaperReview.objects.get(id=rid).proposal_score, 30)
        proposal.refresh_from_db()
        self.assertEqual(proposal.qc_score, 27)

        self.assertTrue(self._row(proposal.id)["qc_score_stale"])

    def test_a_manual_proposal_is_never_stale(self):
        r = self.client.post(self.PROPOSALS, {
            "event_code": self.event.event_code, "speaker_name": "Manual",
            "email": "manual@example.com", "qc_score": 12,
        }, format="json")
        self.assertFalse(self._row(r.data["id"])["qc_score_stale"])

    def test_both_scores_unset_is_not_stale(self):
        """
        SQL's NULL = NULL is unknown, not true — a naive equality test would call
        an unscored pair stale.
        """
        review = PaperReview.objects.create(
            event_code=self.event.event_code, speaker_name="Unscored",
            email="unscored@example.com", paper_submission_date=date(2026, 8, 3),
        )
        proposal = ProposalSubmission.objects.create(
            event_code=self.event.event_code, speaker_name="Unscored",
            email="unscored@example.com", source_paper_review=review,
        )
        self.assertIsNone(review.proposal_score)
        self.assertIsNone(proposal.qc_score)
        self.assertFalse(self._row(proposal.id)["qc_score_stale"])

    def test_exactly_one_score_unset_is_stale(self):
        review = PaperReview.objects.create(
            event_code=self.event.event_code, speaker_name="Half",
            email="half@example.com", paper_submission_date=date(2026, 8, 4),
            closeness_to_topic=7,
        )
        proposal = ProposalSubmission.objects.create(
            event_code=self.event.event_code, speaker_name="Half",
            email="half@example.com", source_paper_review=review, qc_score=None,
        )
        self.assertEqual(review.proposal_score, 7)
        self.assertTrue(self._row(proposal.id)["qc_score_stale"])

    def _make_stale_pair(self, i):
        review = PaperReview.objects.create(
            event_code=self.event.event_code, speaker_name=f"S{i}",
            email=f"s{i}@example.com", paper_submission_date=date(2026, 8, 5),
            closeness_to_topic=3,
        )
        return ProposalSubmission.objects.create(
            event_code=self.event.event_code, speaker_name=f"S{i}",
            email=f"s{i}@example.com", source_paper_review=review, qc_score=1,
        )

    def _paper_review_statements(self):
        """(row count, statements that touched paper_reviews) for one list call."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            rows = self.client.get(self.PROPOSALS).data["results"]
        return rows, [q["sql"] for q in captured.captured_queries
                      if "paper_reviews" in q["sql"]]

    def test_the_flag_adds_no_per_row_query(self):
        """
        No N+1: the flag is a JOIN, so the number of statements touching
        paper_reviews must not move when the page grows.

        Measured as a comparison rather than as a fixed total, because a total
        would also be pinning the pre-existing per-row Event lookup in
        get_event_name — a separate issue, and not this annotation's. Two
        statements carry the join (the pagination COUNT and the page itself); what
        matters is that four rows cost exactly what one row costs.
        """
        self._make_stale_pair(0)
        one_row, one_stmts = self._paper_review_statements()

        for i in range(1, 4):
            self._make_stale_pair(i)
        four_rows, four_stmts = self._paper_review_statements()

        self.assertEqual(len(one_row), 1)
        self.assertEqual(len(four_rows), 4)
        self.assertTrue(all(row["qc_score_stale"] for row in four_rows))
        self.assertEqual(len(four_stmts), len(one_stmts), four_stmts)


class ValidationPathTests(_Base):
    """A4 — the proposal goes through its own form, not Zoho's `insert into`."""

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_both_rows_resolve_the_event_code_to_the_same_canonical_spelling(self):
        r = self.create_review(event_code="afs-js")
        self.assertEqual(r.status_code, 201, r.content)
        review = PaperReview.objects.get(id=r.data["id"])
        proposal = ProposalSubmission.objects.get()
        self.assertEqual(review.event_code, "AFS - JS")
        self.assertEqual(proposal.event_code, "AFS - JS")

    def test_a_proposal_side_validation_failure_rolls_the_review_back(self):
        """
        A5. The review must not survive its proposal failing — a review whose
        proposal silently failed is a gap nobody would notice.
        """
        before = PaperReview.objects.count()
        with patch(
            "proposal_submission.serializers.ProposalSubmissionSerializer"
            ".validate_speaker_name",
            side_effect=drf_serializers.ValidationError("simulated refusal"),
        ):
            r = self.create_review()

        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("detail", r.data)
        self.assertIn("proposal submission", str(r.data["detail"]).lower())
        # And it names what failed, not just that something did.
        self.assertIn("speaker_name", str(r.data.get("proposal_submission", "")))
        self.assertEqual(PaperReview.objects.count(), before)
        self.assertEqual(ProposalSubmission.objects.count(), 0)

    def test_nothing_is_logged_when_the_pair_rolls_back(self):
        before = ActionLog.objects.count()
        with patch(
            "proposal_submission.serializers.ProposalSubmissionSerializer"
            ".validate_speaker_name",
            side_effect=drf_serializers.ValidationError("simulated refusal"),
        ):
            self.create_review()
        self.assertEqual(ActionLog.objects.count(), before)


class OneProposalPerReviewTests(_Base):
    """A7 — a review generates at most one proposal, and it says so."""

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_the_bridge_is_idempotent(self):
        review = PaperReview.objects.create(
            event_code=self.event.event_code, speaker_name="Twice",
            email="twice@example.com", paper_submission_date=date(2026, 8, 6),
        )
        request = _request_for(self.user)
        with transaction.atomic():
            first, created_first, _ = create_proposal_for_review(review, request)
        with transaction.atomic():
            second, created_second, _ = create_proposal_for_review(review, request)

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ProposalSubmission.objects.count(), 1)

    def test_the_database_refuses_a_second_proposal_for_one_review(self):
        self.create_review()
        proposal = ProposalSubmission.objects.get()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProposalSubmission.objects.create(
                    event_code=proposal.event_code, speaker_name="Sneaky",
                    email="sneaky@example.com",
                    source_paper_review=proposal.source_paper_review,
                )

    def test_editing_a_review_does_not_generate_another_proposal(self):
        """Both Zoho workflows are `on add`; an edit is not an add."""
        rid = self.create_review().data["id"]
        self.client.patch(f"{self.LIST}{rid}/", {"theme": "changed"}, format="json")
        self.assertEqual(ProposalSubmission.objects.count(), 1)

    def test_the_response_reports_what_the_workflow_did(self):
        r = self.create_review()
        block = r.data["proposal_submission"]
        self.assertTrue(block["created"])
        self.assertEqual(block["id"], ProposalSubmission.objects.get().id)


class AuthorVisibilityTests(_Base):
    """A8 — in scope by construction, asserted rather than assumed."""

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_the_generated_proposal_is_immediately_visible_to_its_author(self):
        self.create_review()
        proposal = ProposalSubmission.objects.get()

        listing = self.client.get(self.PROPOSALS)
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertIn(proposal.id, [row["id"] for row in listing.data["results"]])
        self.assertEqual(
            self.client.get(f"{self.PROPOSALS}{proposal.id}/").status_code, 200)

    def test_it_carries_the_same_event_code_the_scope_granted(self):
        r = self.create_review()
        review = PaperReview.objects.get(id=r.data["id"])
        proposal = ProposalSubmission.objects.get()
        self.assertEqual(proposal.event_code, review.event_code)
        self.assertIn(proposal.event_code, permitted_event_codes(self.user))


class DuplicateWarningTests(_Base):
    """
    A9 — paper review is the main generator of proposals, so tripping the
    (email, event_code) warning is the expected case. It is advisory: never a 400,
    never a constraint, and both proposals exist afterwards.
    """

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_a_second_review_for_the_same_speaker_and_event_succeeds(self):
        first = self.create_review()
        second = self.create_review()

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(PaperReview.objects.count(), 2)
        self.assertEqual(ProposalSubmission.objects.count(), 2)

        emails = set(ProposalSubmission.objects.values_list("email", flat=True))
        self.assertEqual(emails, {"eli.jasso@example.com"})

    def test_the_warning_is_surfaced_on_the_second_create(self):
        self.create_review()
        second = self.create_review()
        block = second.data["proposal_submission"]
        self.assertEqual(block["duplicate_count"], 1)
        self.assertIn("already exists", block["warning"])

    def test_the_first_create_carries_no_warning(self):
        block = self.create_review().data["proposal_submission"]
        self.assertEqual(block["duplicate_count"], 0)
        self.assertNotIn("warning", block)

    def test_a_duplicate_in_another_event_is_not_counted(self):
        self.create_review()
        second = self.create_review(event_code=self.other_event.event_code)
        self.assertEqual(second.data["proposal_submission"]["duplicate_count"], 0)


class ActionLogTests(_Base):
    """A10 — ONE entry, naming both ids."""

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_exactly_one_entry_naming_both_ids(self):
        before = ActionLog.objects.count()
        r = self.create_review()
        self.assertEqual(ActionLog.objects.count(), before + 1)

        log = ActionLog.objects.latest("id")
        review_id = r.data["id"]
        proposal_id = ProposalSubmission.objects.get().id
        self.assertIn(f"#{review_id}", log.action)
        self.assertIn(f"#{proposal_id}", log.action)
        self.assertIn("paper review", log.action.lower())
        self.assertIn("proposal submission", log.action.lower())
        self.assertEqual(log.user, self.user)


def _request_for(user):
    """A DRF-shaped request for the bridge's serializer context."""
    from rest_framework.test import APIRequestFactory

    request = APIRequestFactory().post("/api/paper-reviews/")
    request.user = user
    return request


class UnassignedEventTests(_Base):
    """
    The generated proposal is scoped the same way the review is, so a review the
    author may file always yields a proposal the author may see. The inverse — an
    event the author is not assigned to — is refused at the review, before any
    proposal exists.
    """

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_no_proposal_is_created_when_the_review_is_refused(self):
        make_event("QQQ - XX", "Not Mine")
        r = self.client.post(self.LIST, self.payload(event_code="QQQ - XX"),
                             format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ProposalSubmission.objects.count(), 0)
        self.assertEqual(PaperReview.objects.count(), 0)
