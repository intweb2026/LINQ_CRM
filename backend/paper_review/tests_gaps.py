"""
paper_review/tests_gaps.py
─────────────────────────────
A3 — every MR leak surface for internal_footnotes; A4 — the serializer contract
guard; A5 — score/grade computation. Split from tests.py the same way
proposal_submission keeps tests.py and tests_gaps.py apart.
"""
from datetime import date

from django.contrib.auth import get_user_model

from paper_review.models import PaperReview
from paper_review.serializers import PaperReviewSerializer
from paper_review.tests import _Base, make_event

U = get_user_model()


# ══ A3. MR FIELD STRIPPING — ALL LEAK SURFACES ═══════════════════════════════

class MRFieldLeakTests(_Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mr = U.objects.create_user(
            username="gaps_mr", password="x", email="gapsmr@example.com",
            role="market_research", team=cls.role)
        cls.mr.assigned_events.set([cls.event])
        cls.row = PaperReview.objects.create(
            event_code=cls.event.event_code, speaker_name="Has Notes",
            email="hn@example.com", paper_submission_date=date(2026, 8, 1),
            internal_footnotes="original notes",
            feedback_to_speaker="visible to everyone",
        )

    # ── Surface 1: absent from serializer output, not blanked ────────────────

    def test_absent_from_output_entirely_for_a_non_mr_user(self):
        self.client.force_authenticate(user=self.user)
        row = self.client.get(f"{self.LIST}{self.row.id}/").data
        self.assertNotIn("internal_footnotes", row,
                         "must be ABSENT, not present-and-blank — a present empty "
                         "string would let a non-MR user infer the field is empty")

    def test_readable_and_present_for_an_mr_user(self):
        self.client.force_authenticate(user=self.mr)
        row = self.client.get(f"{self.LIST}{self.row.id}/").data
        self.assertEqual(row["internal_footnotes"], "original notes")

    # ── Surface 2: rejected as a filter param ─────────────────────────────────

    def test_filtering_on_internal_footnotes_is_400_for_non_mr(self):
        self.client.force_authenticate(user=self.user)
        for params in ({"internal_footnotes": "original"},
                       {"internal_footnotes__icontains": "orig"}):
            with self.subTest(params=params):
                r = self.client.get(self.LIST, params)
                self.assertEqual(r.status_code, 400, r.content)

    def test_mr_user_may_filter_by_it_without_error(self):
        """
        DjangoFilterBackend is not wired for this field, so it is not an active
        filter — the point under test is only that the GUARD does not fire for MR,
        i.e. the request is not rejected the way a non-MR request is.
        """
        self.client.force_authenticate(user=self.mr)
        r = self.client.get(self.LIST, {"internal_footnotes": "original"})
        self.assertEqual(r.status_code, 200, r.content)

    # ── Surface 3: rejected in ?ordering= ──────────────────────────────────────

    def test_ordering_by_internal_footnotes_is_400_for_non_mr(self):
        self.client.force_authenticate(user=self.user)
        for value in ("internal_footnotes", "-internal_footnotes",
                     "speaker_name,internal_footnotes"):
            with self.subTest(ordering=value):
                r = self.client.get(self.LIST, {"ordering": value})
                self.assertEqual(r.status_code, 400, r.content)

    def test_mr_user_may_order_by_it(self):
        self.client.force_authenticate(user=self.mr)
        r = self.client.get(self.LIST, {"ordering": "internal_footnotes"})
        self.assertEqual(r.status_code, 200, r.content)

    # ── Surface 4: excluded from filter_schema output ─────────────────────────

    def test_filter_schema_hides_internal_footnotes_for_non_mr(self):
        self.client.force_authenticate(user=self.user)
        fields = self.client.get(f"{self.LIST}filter_schema/").data["fields"]
        self.assertNotIn("internal_footnotes", fields)

    def test_filter_schema_shows_internal_footnotes_to_mr(self):
        self.client.force_authenticate(user=self.mr)
        fields = self.client.get(f"{self.LIST}filter_schema/").data["fields"]
        self.assertIn("internal_footnotes", fields)

    # ── Surface 5: echo-vs-edit — not wiped by an edit to a different field ───

    def test_non_mr_edit_of_another_field_preserves_the_stored_notes(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.patch(f"{self.LIST}{self.row.id}/",
                              {"company_name": "New Co",
                               "internal_footnotes": ""},   # form echo
                              format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.internal_footnotes, "original notes")
        self.assertEqual(self.row.company_name, "New Co")

    def test_non_mr_attempt_to_write_real_content_is_refused_loudly(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.patch(f"{self.LIST}{self.row.id}/",
                              {"internal_footnotes": "sneaky content"},
                              format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("internal_footnotes", r.data)
        self.row.refresh_from_db()
        self.assertEqual(self.row.internal_footnotes, "original notes")

    def test_non_mr_create_with_real_mr_content_is_400(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self.LIST, self.payload(
            internal_footnotes="sneaky at create"), format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("internal_footnotes", r.data)

    def test_non_mr_create_with_blank_mr_field_is_fine(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self.LIST, self.payload(internal_footnotes=""),
                             format="json")
        self.assertEqual(r.status_code, 201, r.content)

    # ── MR user: readable, writable, clearable ────────────────────────────────

    def test_mr_user_can_write_new_content(self):
        self.client.force_authenticate(user=self.mr)
        r = self.client.patch(f"{self.LIST}{self.row.id}/",
                              {"internal_footnotes": "updated by MR"},
                              format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.internal_footnotes, "updated by MR")

    def test_mr_user_can_clear_it_to_blank(self):
        """
        The drop-blank-echo rule must NOT apply to MR users, or they could never
        delete their own notes.
        """
        self.client.force_authenticate(user=self.mr)
        r = self.client.patch(f"{self.LIST}{self.row.id}/",
                              {"internal_footnotes": ""}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.internal_footnotes, "")

    def test_admin_can_write_and_clear_it_too(self):
        admin = U.objects.create_user(
            username="gaps_admin", password="x", email="gapsadmin@example.com",
            role="admin", team=self.role)
        self.client.force_authenticate(user=admin)
        r = self.client.patch(f"{self.LIST}{self.row.id}/",
                              {"internal_footnotes": ""}, format="json")
        self.assertEqual(r.status_code, 200, r.content)

    # ── feedback_to_speaker stays UNRESTRICTED ────────────────────────────────

    def test_feedback_to_speaker_is_not_stripped_for_a_non_mr_user(self):
        self.client.force_authenticate(user=self.user)
        row = self.client.get(f"{self.LIST}{self.row.id}/").data
        self.assertIn("feedback_to_speaker", row)
        self.assertEqual(row["feedback_to_speaker"], "visible to everyone")

    def test_feedback_to_speaker_is_writable_by_a_non_mr_user(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.patch(f"{self.LIST}{self.row.id}/",
                              {"feedback_to_speaker": "an update"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.feedback_to_speaker, "an update")

    def test_ordering_by_feedback_to_speaker_is_never_rejected(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.get(self.LIST, {"ordering": "feedback_to_speaker"})
        # Not one of the restricted fields, but also not a real ordering_fields
        # entry — DRF's OrderingFilter silently ignores an unknown ordering key
        # rather than erroring, so 200 (not 400) is the correct assertion here.
        self.assertEqual(r.status_code, 200, r.content)


# ══ A4. SERIALIZER CONTRACT ═══════════════════════════════════════════════════

SYSTEM_FIELDS = {"id", "created_at", "updated_at", "created_by", "updated_by"}


def model_field_names(exclude_system=True):
    names = {
        f.name for f in PaperReview._meta.get_fields()
        if getattr(f, "concrete", False)
    }
    return names - SYSTEM_FIELDS if exclude_system else names


class SerializerContractTests(_Base):
    """
    Guard against shipping a serializer that silently omits a model field — this
    exact class of bug is WHY this module was never wired: it shipped with a
    self-recursive validate() that no test ever exercised.
    """

    def test_serializer_declares_every_non_system_model_field(self):
        declared = set(PaperReviewSerializer().get_fields())
        missing = model_field_names() - declared
        self.assertEqual(
            missing, set(),
            f"serializer is missing model field(s): {sorted(missing)}")

    def test_every_editable_field_is_a_real_model_field(self):
        from paper_review.serializers import EDITABLE_FIELDS
        every = model_field_names(exclude_system=False)
        for field in EDITABLE_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, every)


# ══ A5. SCORE AND GRADE ═══════════════════════════════════════════════════════

class ScoreAndGradeTests(_Base):
    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_score_is_recomputed_ignoring_a_client_supplied_value(self):
        r = self.create_review(proposal_score=999)
        self.assertEqual(r.status_code, 201, r.content)
        review = PaperReview.objects.get(id=r.data["id"])
        self.assertEqual(review.proposal_score, 27)     # 9+2+9+1+1+5, never 999
        self.assertEqual(r.data["proposal_score"], 27)

    def test_score_is_recomputed_on_update_too(self):
        rid = self.create_review().data["id"]
        r = self.client.patch(f"{self.LIST}{rid}/",
                              {"closeness_to_topic": 10, "proposal_score": 1},
                              format="json")
        self.assertEqual(r.status_code, 200, r.content)
        review = PaperReview.objects.get(id=rid)
        self.assertEqual(review.proposal_score, 28)     # 10+2+9+1+1+5
        self.assertNotEqual(review.proposal_score, 1)

    def test_the_serializer_requires_all_six_criteria_matching_the_zoho_form(self):
        """
        REPORTED, NOT A BUG: serializers.py's REQUIRED_FIELDS marks all six
        criteria required with allow_null=False — "marked * in the Zoho form",
        per its own comment — so submitting any of them as null through the API
        is a 400, not a silently-unscored row. That intentionally makes the
        model's null-friendly computed_score() unreachable from THIS path; the two
        tests below exercise it at the model layer instead, which is the path the
        same comment names as the reason the model stays nullable: "so historical
        imports can land incomplete rows."
        """
        r = self.create_review(closeness_to_topic=None)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("closeness_to_topic", r.data)

    def test_model_level_all_six_criteria_null_leaves_the_score_null_not_zero(self):
        """
        The path this exercises is a direct ORM write (e.g. a future historical
        import), which is exactly why the MODEL keeps these fields nullable while
        the serializer enforces them as required for ordinary form submissions.
        """
        review = PaperReview.objects.create(
            event_code=self.event.event_code, speaker_name="Unscored",
            email="unscored.a5@example.com",
            paper_submission_date=date(2026, 8, 1),
        )
        self.assertIsNone(review.proposal_score)

    def test_model_level_partial_criteria_sum_only_the_filled_ones(self):
        review = PaperReview.objects.create(
            event_code=self.event.event_code, speaker_name="Partial",
            email="partial.a5@example.com",
            paper_submission_date=date(2026, 8, 1),
            closeness_to_topic=8, case_study_results_examples=3,
        )
        self.assertEqual(review.proposal_score, 11)      # 8 + 3, nulls excluded

    def test_each_criterion_is_rejected_at_max_plus_one(self):
        from paper_review.models import CRITERIA
        for field, maximum in CRITERIA:
            with self.subTest(field=field):
                r = self.create_review(**{field: maximum + 1})
                self.assertEqual(r.status_code, 400, r.content)
                self.assertIn(field, r.data)

    def test_each_criterion_accepts_its_own_maximum(self):
        from paper_review.models import CRITERIA
        for field, maximum in CRITERIA:
            with self.subTest(field=field):
                r = self.create_review(email=f"{field}@example.com",
                                       **{field: maximum})
                self.assertEqual(r.status_code, 201, r.content)

    def test_negative_criterion_is_rejected(self):
        r = self.create_review(closeness_to_topic=-1)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("closeness_to_topic", r.data)

    def test_grade_is_accepted_as_submitted(self):
        r = self.create_review(grade="A")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PaperReview.objects.get(id=r.data["id"]).grade, "A")

    def test_grade_is_never_overwritten_by_a_derived_value(self):
        """
        The score computed here (27/45 = 60%) would derive to "B" under the
        form's OWN gradeFor() bands, were grade derived server-side. It is not —
        submitting "D" alongside a 60% score must store "D" verbatim.
        """
        r = self.create_review(grade="D")
        self.assertEqual(r.status_code, 201, r.content)
        review = PaperReview.objects.get(id=r.data["id"])
        self.assertEqual(review.proposal_score, 27)
        self.assertEqual(review.grade, "D")

    def test_grade_survives_a_rescoring_update_unless_explicitly_changed(self):
        rid = self.create_review(grade="C").data["id"]
        r = self.client.patch(f"{self.LIST}{rid}/",
                              {"closeness_to_topic": 10}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        review = PaperReview.objects.get(id=rid)
        self.assertEqual(review.grade, "C")             # untouched by the rescoring
        self.assertEqual(review.proposal_score, 28)
