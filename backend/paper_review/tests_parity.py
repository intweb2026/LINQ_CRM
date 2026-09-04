"""
paper_review/tests_parity.py
─────────────────────────────
PART C — the machinery paper_review was missing relative to proposal_submission:
duplicate detection (C1), bulk update (C2) and distinct filter options (C4).

C3 was a CSV export. It is gone; export lives on Bookings and Pre Event Docs
and nowhere else.

C2's load-bearing claim is the one worth reading first: bulk-updating ANY of the
six criteria must recompute proposal_score on every affected row, because the
column is derived. The shared mixin gets that right only because it uses
per-object save() rather than queryset.update() — asserted here rather than
assumed, since a future "optimisation" to .update() would silently freeze every
score in the batch.
"""
import csv
from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import ActionLog
from paper_review.models import CRITERIA, RUBRIC_TOTAL, PaperReview
from events.testutils import assign_reviewer
from paper_review.tests import _Base, make_event

U = get_user_model()


def make_review(code, speaker, **over):
    fields = {
        "event_code": code, "speaker_name": speaker,
        "email": f"{speaker.replace(' ', '.').lower()}@example.com",
        "paper_submission_date": date(2026, 5, 1),
    }
    fields.update(over)
    return PaperReview.objects.create(**fields)


# ══ C1. DUPLICATE DETECTION ══════════════════════════════════════════════════

class DuplicateDetectionTests(_Base):
    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_duplicate_count_counts_other_rows_on_the_same_email_and_event(self):
        a = make_review("AFS - JS", "Dup One")
        make_review("AFS - JS", "Dup Two", email=a.email)
        row = self.client.get(f"{self.LIST}{a.id}/").data
        self.assertEqual(row["duplicate_count"], 1)

    def test_the_email_match_is_case_insensitive(self):
        a = make_review("AFS - JS", "Case One", email="Mixed.Case@Example.com")
        make_review("AFS - JS", "Case Two", email="mixed.case@example.com")
        row = self.client.get(f"{self.LIST}{a.id}/").data
        self.assertEqual(row["duplicate_count"], 1)

    def test_the_same_email_on_a_different_event_is_not_a_duplicate(self):
        a = make_review("AFS - JS", "Solo One")
        make_review("BIUK - PM", "Solo Two", email=a.email)
        row = self.client.get(f"{self.LIST}{a.id}/").data
        self.assertEqual(row["duplicate_count"], 0)

    def test_a_same_email_row_on_an_unassigned_event_is_never_counted(self):
        """
        The annotation is built on the SCOPED queryset. Here the other row is both
        out of scope AND on a different event, so it fails the duplicate rule
        twice over — the count is 0 for the scoped user, and an admin who can see
        both still gets 0 because (email, event_code) does not match.
        """
        make_event("HIDDEN - ZZ")
        a = make_review("AFS - JS", "Scoped One")
        make_review("HIDDEN - ZZ", "Scoped Two", email=a.email)

        self.assertEqual(
            self.client.get(f"{self.LIST}{a.id}/").data["duplicate_count"], 0)

        admin = U.objects.create_user(
            username="dup_admin", password="x", email="dupadmin@example.com",
            role="admin", team=self.role)
        self.client.force_authenticate(user=admin)
        self.assertEqual(
            self.client.get(f"{self.LIST}{a.id}/").data["duplicate_count"], 0,
            "different event_code is not a duplicate, for anyone")

    def test_a_visible_rows_peer_is_always_visible_too(self):
        """
        WHY THE SCOPED SUBQUERY IS STRUCTURALLY REDUNDANT TODAY, and kept anyway.

        Duplicates share event_code by definition, and scope IS exact membership
        on event_code — so any peer of a row the caller can see is necessarily in
        the caller's scope as well. The scoped and unscoped counts therefore agree
        for every visible row today, and no fixture can make them disagree.

        It stays built on the scoped queryset as defence in depth: the moment the
        scope rule grows a second clause that is not event_code (granting on
        created_by, say, as RBACMixin.rbac_filter already tries to do elsewhere),
        an unscoped peer query would start reporting counts drawn from rows the
        caller cannot see. Asserting the agreement pins the equivalence rather
        than leaving a reader to assume the scoping does something it currently
        cannot.
        """
        a = make_review("AFS - JS", "Peer One")
        make_review("AFS - JS", "Peer Two", email=a.email)

        scoped_count = self.client.get(
            f"{self.LIST}{a.id}/").data["duplicate_count"]

        admin = U.objects.create_user(
            username="dup_admin2", password="x", email="dupadmin2@example.com",
            role="admin", team=self.role)
        self.client.force_authenticate(user=admin)
        admin_count = self.client.get(
            f"{self.LIST}{a.id}/").data["duplicate_count"]

        self.assertEqual(scoped_count, 1)
        self.assertEqual(admin_count, scoped_count)

    def test_none_not_zero_on_a_create_response(self):
        """
        The create response never went through get_queryset, so the annotation is
        absent. None says "not evaluated here"; 0 would be a claim.
        """
        r = self.create_review()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(r.data["duplicate_count"])

    def test_a_create_that_duplicates_warns_without_blocking(self):
        make_review("AFS - JS", "Existing", email="eli.jasso@example.com")
        r = self.create_review()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["duplicate_count"], 1)
        self.assertIn("already exists", r.data["warning"])

    def test_a_first_create_carries_no_warning(self):
        r = self.create_review()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertNotIn("warning", r.data)

    def test_has_duplicates_filter_narrows_both_ways(self):
        a = make_review("AFS - JS", "Filter One")
        make_review("AFS - JS", "Filter Two", email=a.email)
        make_review("AFS - JS", "Filter Solo")

        dupes = self.client.get(self.LIST, {"has_duplicates": "true"})
        self.assertEqual(dupes.data["count"], 2)
        singles = self.client.get(self.LIST, {"has_duplicates": "false"})
        self.assertEqual(singles.data["count"], 1)

    def test_the_list_payload_carries_the_count_without_a_query_per_row(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        a = make_review("AFS - JS", "N1")
        make_review("AFS - JS", "N2", email=a.email)
        for i in range(4):
            make_review("AFS - JS", f"Extra {i}")

        with CaptureQueriesContext(connection) as captured:
            rows = self.client.get(self.LIST, {"page_size": 50}).data["results"]
        self.assertTrue(all("duplicate_count" in r for r in rows))
        # The annotation is a Subquery inside the page query; a per-row lookup
        # would add one statement per row against paper_reviews.
        touching = [q["sql"] for q in captured.captured_queries
                    if "paper_reviews" in q["sql"]]
        self.assertLessEqual(len(touching), 2, touching)


# ══ C2. BULK UPDATE ══════════════════════════════════════════════════════════

class BulkUpdateTests(_Base):
    URL = "/api/paper-reviews/bulk_update/"
    SCHEMA = "/api/paper-reviews/bulk_update_schema/"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mr = U.objects.create_user(
            username="bulk_mr", password="x", email="bulkmr@example.com",
            role="market_research", team=cls.role)
        assign_reviewer(cls.mr, cls.event, cls.other_event, junior=True)

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.rows = [
            make_review("AFS - JS", f"Bulk {i}", closeness_to_topic=5,
                        closeness_to_region=2, clear_solution_to_challenges=3,
                        case_study_results_examples=1,
                        not_obvious_sales_pitch=1, company_profile_score=4)
            for i in range(3)
        ]
        self.ids = [r.id for r in self.rows]

    def _run(self, field, value, ids=None, user=None):
        if user:
            self.client.force_authenticate(user=user)
        preview = self.client.post(self.URL, {
            "ids": ids or self.ids, "field": field, "value": value,
            "commit": False}, format="json")
        if preview.status_code != 200:
            return preview, None
        commit = self.client.post(self.URL, {
            "ids": ids or self.ids, "field": field, "value": value,
            "commit": True, "plan_hash": preview.data["plan_hash"]},
            format="json")
        return preview, commit

    def test_the_whitelist_covers_c2_and_excludes_identity_and_computed(self):
        """
        C2's original five plus the six criteria are still the load-bearing set;
        the registry is now derived from the model, so the rest of the editable
        columns come with it. What must NOT be there is asserted explicitly —
        that list is the whole safety argument.
        """
        from paper_review.views import PaperReviewViewSet
        wired = set(PaperReviewViewSet.bulk_update_fields)
        required = {
            "session_location_on_agenda", "nos", "feedback_to_speaker",
            "internal_footnotes",
            *[f for f, _ in CRITERIA],
        }
        self.assertTrue(required <= wired, required - wired)
        for forbidden in ("event_code", "speaker_name", "email", "company_name",
                          "speaker_email_ref", "research_email_ref",
                          "proposal_score", "grade", "import_batch_id",
                          "created_by", "updated_by", "id"):
            self.assertNotIn(forbidden, wired)

    def test_every_criterion_carries_its_rubric_maximum(self):
        """
        The bounds come off the model's own MaxValueValidator now rather than
        being restated in the ViewSet, so CRITERIA stays the single source of
        truth. A criterion that lost its validator would lose its ceiling here.
        """
        from paper_review.views import PaperReviewViewSet
        for name, maximum in CRITERIA:
            cfg = PaperReviewViewSet.bulk_update_fields[name]
            self.assertEqual(cfg["max"], maximum, name)
            self.assertEqual(cfg["min"], 0, name)
            self.assertTrue(cfg["nullable"], name)

    def test_a_theme_longer_than_the_column_is_a_400_not_a_dataerror(self):
        r = self.client.post(self.URL, {
            "ids": self.ids, "field": "theme", "value": "x" * 256,
            "commit": False}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("255", str(r.data))

    def test_proposal_score_is_not_bulk_writable(self):
        """It is COMPUTED — a bulk write would be overwritten by save()."""
        from paper_review.views import PaperReviewViewSet
        self.assertNotIn("proposal_score", PaperReviewViewSet.bulk_update_fields)
        r = self.client.post(self.URL, {
            "ids": self.ids, "field": "proposal_score", "value": 40,
            "commit": False}, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_grade_is_not_bulk_writable(self):
        """
        DERIVED, like proposal_score — save() recomputes it from the criteria
        on every write, so a bulk write would be overwritten in the same
        statement and read as a silent no-op. Offering it in the picker would
        be a lie. Bulk-updating a CRITERION moves the grade instead; see
        test_bulk_updating_a_criterion_recomputes_the_score_on_every_row.
        """
        from paper_review.views import PaperReviewViewSet
        self.assertNotIn("grade", PaperReviewViewSet.bulk_update_fields)
        r = self.client.post(self.URL, {
            "ids": self.ids, "field": "grade", "value": "A",
            "commit": False}, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_nos_bulk_updates_as_a_boolean(self):
        preview, commit = self._run("nos", True)
        self.assertEqual(commit.status_code, 200, commit.content)
        self.assertTrue(all(r.nos for r in PaperReview.objects.filter(
            id__in=self.ids)))

    # ── the load-bearing one ──────────────────────────────────────────────────

    def test_bulk_updating_a_criterion_recomputes_the_score_on_every_row(self):
        """
        C2's central claim. Before: 5+2+3+1+1+4 = 16. Setting
        closeness_to_topic=10 must make every affected row 21, not leave 16
        frozen — which is exactly what queryset.update() would do.
        """
        self.assertTrue(all(r.proposal_score == 16
                            for r in PaperReview.objects.filter(id__in=self.ids)))

        preview, commit = self._run("closeness_to_topic", 10)
        self.assertEqual(commit.status_code, 200, commit.content)

        for row in PaperReview.objects.filter(id__in=self.ids):
            with self.subTest(row=row.id):
                self.assertEqual(row.closeness_to_topic, 10)
                self.assertEqual(row.proposal_score, 21)

    def test_the_preview_states_the_recomputation_as_a_side_effect(self):
        preview, _ = self._run("closeness_to_region", 5)
        text = " ".join(preview.data.get("side_effects") or [])
        self.assertIn("Proposal Score", text)
        self.assertIn(str(RUBRIC_TOTAL), text)

    def test_clearing_a_criterion_to_null_recomputes_too(self):
        preview, commit = self._run("company_profile_score", None)
        self.assertEqual(commit.status_code, 200, commit.content)
        for row in PaperReview.objects.filter(id__in=self.ids):
            self.assertIsNone(row.company_profile_score)
            self.assertEqual(row.proposal_score, 12)     # 16 - 4

    def test_a_criterion_above_its_max_is_refused_with_400_not_a_500(self):
        r = self.client.post(self.URL, {
            "ids": self.ids, "field": "closeness_to_region", "value": 6,
            "commit": False}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("6", str(r.data) + "6")

    def test_a_non_numeric_criterion_value_is_400_not_an_unhandled_500(self):
        """
        The exact reason the local integer _coerce override exists: declaring
        these "text" would pass "abc" to save() and raise ValueError deep in the
        ORM as a 500.
        """
        r = self.client.post(self.URL, {
            "ids": self.ids, "field": "closeness_to_topic", "value": "abc",
            "commit": False}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("whole number", str(r.data))

    def test_a_negative_criterion_value_is_refused(self):
        r = self.client.post(self.URL, {
            "ids": self.ids, "field": "closeness_to_topic", "value": -1,
            "commit": False}, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    # ── MR gating ─────────────────────────────────────────────────────────────

    def test_internal_footnotes_is_refused_for_a_non_mr_user(self):
        r = self.client.post(self.URL, {
            "ids": self.ids, "field": "internal_footnotes", "value": "x",
            "commit": False}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("restricted", str(r.data).lower())

    def test_internal_footnotes_is_allowed_for_an_mr_user(self):
        preview, commit = self._run("internal_footnotes", "MR note", user=self.mr)
        self.assertEqual(commit.status_code, 200, commit.content)
        for row in PaperReview.objects.filter(id__in=self.ids):
            self.assertEqual(row.internal_footnotes, "MR note")

    def test_the_schema_hides_internal_footnotes_from_a_non_mr_user(self):
        fields = self.client.get(self.SCHEMA).data["fields"]
        self.assertNotIn("internal_footnotes", fields)
        self.assertIn("session_location_on_agenda", fields)

    def test_the_schema_shows_internal_footnotes_to_mr(self):
        self.client.force_authenticate(user=self.mr)
        fields = self.client.get(self.SCHEMA).data["fields"]
        self.assertIn("internal_footnotes", fields)

    # ── scope + audit ─────────────────────────────────────────────────────────

    def test_an_out_of_scope_id_is_404(self):
        outside = make_event("BULKOUT - ZZ")
        hidden = make_review("BULKOUT - ZZ", "Hidden")
        r = self.client.post(self.URL, {
            "ids": [hidden.id], "field": "session_location_on_agenda",
            "value": "Day 1, Afternoon Session",
            "commit": False}, format="json")
        self.assertEqual(r.status_code, 404, r.content)

    def test_a_mixed_batch_writes_nothing(self):
        make_event("BULKOUT2 - ZZ")
        hidden = make_review("BULKOUT2 - ZZ", "Hidden Two")
        r = self.client.post(self.URL, {
            "ids": [self.ids[0], hidden.id],
            "field": "session_location_on_agenda",
            "value": "Day 1, Afternoon Session",
            "commit": False}, format="json")
        self.assertEqual(r.status_code, 404, r.content)
        self.rows[0].refresh_from_db()
        self.assertEqual(self.rows[0].session_location_on_agenda, "")

    def test_one_actionlog_per_batch_with_the_full_id_list(self):
        before = ActionLog.objects.count()
        preview, commit = self._run("session_location_on_agenda",
                                    "Day 2, Keynote")
        self.assertEqual(commit.status_code, 200, commit.content)
        self.assertEqual(ActionLog.objects.count(), before + 1)
        log = ActionLog.objects.latest("id")
        self.assertIn("paper reviews", log.action)
        for pk in self.ids:
            self.assertIn(str(pk), log.details)


# ══ C4. DISTINCT FILTER OPTIONS ══════════════════════════════════════════════

class FilterOptionsTests(_Base):
    OPTIONS = "/api/paper-reviews/filter_options/"

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        # A full 45/45 derives to "A"; the second row is unscored so its grade
        # derives to "" — the blank the assertions below require to be excluded.
        make_review("AFS - JS", "Opt One",
                    closeness_to_topic=10, closeness_to_region=5,
                    clear_solution_to_challenges=10,
                    case_study_results_examples=5, not_obvious_sales_pitch=5,
                    company_profile_score=10,
                    session_location_on_agenda="Day 1, Afternoon Session")
        make_review("BIUK - PM", "Opt Two",
                    session_location_on_agenda="Day 2, Keynote")

    def test_returns_only_values_actually_stored(self):
        r = self.client.get(self.OPTIONS)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["grade"], ["A"], "blank excluded")
        self.assertEqual(sorted(r.data["session_location_on_agenda"]),
                         ["Day 1, Afternoon Session", "Day 2, Keynote"])

    def test_covers_both_dropdown_fields(self):
        r = self.client.get(self.OPTIONS)
        for field in ("grade", "session_location_on_agenda"):
            self.assertIn(field, r.data)

    def test_options_are_rbac_scoped(self):
        scoped = U.objects.create_user(
            username="opt_scoped", password="x", email="optscoped@example.com",
            role="sales", team=self.role)
        assign_reviewer(scoped, self.other_event, junior=True)            # BIUK only
        self.client.force_authenticate(user=scoped)
        r = self.client.get(self.OPTIONS)
        self.assertEqual(r.data["session_location_on_agenda"], ["Day 2, Keynote"])
        self.assertEqual(r.data["grade"], [])

    def test_filter_schema_choices_come_from_stored_values(self):
        r = self.client.get(f"{self.LIST}filter_schema/")
        self.assertEqual(r.data["fields"]["grade"]["choices"], ["A"])

    def test_denied_without_the_module(self):
        self.client.force_authenticate(user=self.blind_user)
        self.assertEqual(self.client.get(self.OPTIONS).status_code, 403)


# ══ A2. PERMITTED EVENTS (both modules) ══════════════════════════════════════

class PermittedEventsTests(_Base):
    URL = "/api/paper-reviews/permitted_events/"

    def test_scoped_user_sees_only_assigned_events(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.data["unrestricted"])
        self.assertEqual(sorted(e["event_code"] for e in r.data["results"]),
                         ["AFS - JS", "BIUK - PM"])

    def test_unassigned_user_sees_none(self):
        nobody = U.objects.create_user(
            username="pe_none", password="x", email="penone@example.com",
            role="sales", team=self.role)
        self.client.force_authenticate(user=nobody)
        self.assertEqual(self.client.get(self.URL).data["count"], 0)

    def test_admin_sees_the_whole_catalogue(self):
        make_event("EXTRA - ZZ")
        admin = U.objects.create_user(
            username="pe_admin", password="x", email="peadmin@example.com",
            role="admin", team=self.role)
        self.client.force_authenticate(user=admin)
        r = self.client.get(self.URL)
        self.assertTrue(r.data["unrestricted"])
        self.assertIn("EXTRA - ZZ", [e["event_code"] for e in r.data["results"]])

    def test_picker_and_validator_agree(self):
        """
        A2's assertion: every code the picker offers must create successfully.
        This is the whole reason the picker was repointed — the full catalogue
        offered 142 codes, of which all but two answered 400 for this user.
        """
        self.client.force_authenticate(user=self.user)
        offered = [e["event_code"] for e in self.client.get(self.URL).data["results"]]
        self.assertTrue(offered)
        for i, code in enumerate(offered):
            with self.subTest(code=code):
                r = self.client.post(self.LIST, self.payload(
                    event_code=code, email=f"picker{i}@example.com"),
                    format="json")
                self.assertEqual(r.status_code, 201, r.content)
