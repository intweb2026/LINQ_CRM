"""
proposal_submission/tests_gaps.py
──────────────────────────────────
The gap-closing pass: resolver delegation, anchored event_code filtering,
MR-only field visibility, the shared mixins, duplicate(), and the serializer
contract guard.

Split from tests.py so the original CRUD/validation suite stays readable, the
same way ticket_central keeps tests.py and tests_bulk_update.py apart.
"""
from datetime import date

from accounts.models import ActionLog
from proposal_submission.models import ProposalSubmission
from proposal_submission.tests import _Base, make_event
from events.testutils import assign_reviewer

# source_paper_review and import_batch_id both sit with the audit columns rather
# than the business ones: both are provenance, both are written by something
# OTHER than the ordinary create/update path (proposal_bridge.py and
# import_commit respectively), both are read-only on the serializer, and neither
# is copied by duplicate() — a clone is a new proposal, not a second row claiming
# the same review or the same import batch.
SYSTEM_FIELDS = {"id", "created_at", "updated_at", "created_by", "updated_by",
                 "source_paper_review", "import_batch_id"}


def model_field_names(exclude_system=True):
    names = {
        f.name for f in ProposalSubmission._meta.get_fields()
        if getattr(f, "concrete", False)
    }
    return names - SYSTEM_FIELDS if exclude_system else names


class EventCodeAnchoredFilterTests(_Base):
    """
    ?event_code=BIU must return BIU and BIU/GS - PM, and never BIUK - PM.

    Plain icontains returned BIUK for a BIU query — a different event in a
    different country. The filter now applies the resolver's own boundary regex.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Assigned so this suite tests the FILTER, not the scope — scoping has its
        # own suite in tests_scope.py.
        cls.assign_events(make_event("BIU"), make_event("BIU/GS - PM"))
        for code in ("BIU", "BIU/GS - PM", "BIUK - PM"):
            ProposalSubmission.objects.create(
                event_code=code, speaker_name=f"S {code}",
                email="s@x.com", submission_date=date(2026, 4, 1),
            )

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def _codes_for(self, query):
        r = self.client.get(self.LIST, {"event_code": query})
        self.assertEqual(r.status_code, 200, r.content)
        return sorted(row["event_code"] for row in r.data["results"])

    def test_biu_matches_biu_and_biu_gs_but_never_biuk(self):
        got = self._codes_for("BIU")
        self.assertEqual(got, ["BIU", "BIU/GS - PM"])
        self.assertNotIn("BIUK - PM", got)

    def test_biuk_matches_only_biuk(self):
        self.assertEqual(self._codes_for("BIUK"), ["BIUK - PM"])

    def test_anchored_filter_parametrised(self):
        cases = [
            ("BIU",         ["BIU", "BIU/GS - PM"]),
            ("biu",         ["BIU", "BIU/GS - PM"]),   # case-insensitive
            ("BIUK",        ["BIUK - PM"]),
            ("BIU/GS",      ["BIU/GS - PM"]),
            ("BIU/GS - PM", ["BIU/GS - PM"]),
            ("BIUK - PM",   ["BIUK - PM"]),
            ("BI",          []),                       # followed by alphanumeric
            ("IU",          []),                       # preceded by alphanumeric
            ("ZZZ",         []),
        ]
        for query, expected in cases:
            with self.subTest(event_code=query):
                self.assertEqual(self._codes_for(query), expected)

    def test_blank_filter_is_a_no_op(self):
        r = self.client.get(self.LIST, {"event_code": ""})
        self.assertEqual(r.data["count"], 3)


class EventCodeResolverDelegationTests(_Base):
    """The serializer resolves through webhooks/event_resolver, not its own copy."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.assign_events(make_event("BIU/GS - PM"))

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_boundary_match_resolves_to_the_canonical_code(self):
        """'BIU' has no exact Event but boundary-matches exactly one."""
        r = self.client.post(self.LIST, self.payload(event_code="BIU"), format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["event_code"], "BIU/GS - PM")

    def test_ambiguous_code_is_rejected_not_guessed(self):
        make_event("BIU - RS")          # now BIU boundary-matches two editions
        r = self.client.post(self.LIST, self.payload(event_code="BIU"), format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("event_code", r.data)
        self.assertIn("Ambiguous", str(r.data["event_code"]))

    def test_no_match_names_the_prefilter_candidates(self):
        r = self.client.post(self.LIST, self.payload(event_code="BIUKX"),
                             format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Prefilter candidates", str(r.data["event_code"]))


class MRFieldVisibilityTests(_Base):
    """slot_recommendation_mr / internal_footnotes_mr are MR + admin only."""

    MR_FIELDS = ("slot_recommendation_mr", "internal_footnotes_mr")

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth import get_user_model
        U = get_user_model()
        cls.mr_user = U.objects.create_user(
            username="mr_user", password="x", email="mr@example.com",
            team=cls.role, role="market_research",
        )
        # MR-field visibility and ROW scope are independent: the market_research
        # role unlocks the MR columns, it does not widen which rows are visible.
        # Without an assignment this user would 404 on the row before ever
        # reaching the field-stripping logic.
        assign_reviewer(cls.mr_user, cls.event, cls.other_event, junior=True)
        cls.admin_user = U.objects.create_user(
            username="admin_user", password="x", email="ad@example.com",
            team=cls.role, role="admin",
        )
        cls.row = ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Held Notes",
            email="h@x.com", submission_date=date(2026, 4, 1),
            slot_recommendation_mr="Put on the main stage",
            internal_footnotes_mr="Weak on delivery",
        )

    def test_mr_and_admin_can_read_the_fields(self):
        for user in (self.mr_user, self.admin_user):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                r = self.client.get(f"{self.LIST}{self.row.id}/")
                self.assertEqual(r.status_code, 200)
                for f in self.MR_FIELDS:
                    self.assertIn(f, r.data)
                self.assertEqual(r.data["internal_footnotes_mr"], "Weak on delivery")

    def test_other_roles_get_the_fields_stripped_not_blanked(self):
        self.client.force_authenticate(user=self.user)      # not MR, not admin
        r = self.client.get(f"{self.LIST}{self.row.id}/")
        self.assertEqual(r.status_code, 200)
        for f in self.MR_FIELDS:
            self.assertNotIn(f, r.data, f"{f} must be ABSENT, not blanked")

    def test_stripped_in_list_view_too(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.get(self.LIST)
        for row in r.data["results"]:
            for f in self.MR_FIELDS:
                self.assertNotIn(f, row)

    def test_mr_can_write_the_fields(self):
        self.client.force_authenticate(user=self.mr_user)
        r = self.client.patch(f"{self.LIST}{self.row.id}/",
                              {"internal_footnotes_mr": "Reassessed"},
                              format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.internal_footnotes_mr, "Reassessed")

    def test_other_roles_cannot_write_content_to_them(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.patch(f"{self.LIST}{self.row.id}/",
                              {"internal_footnotes_mr": "sneaky"},
                              format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("internal_footnotes_mr", r.data)
        self.row.refresh_from_db()
        self.assertEqual(self.row.internal_footnotes_mr, "Weak on delivery")

    # ── The same rule on the bulk path ────────────────────────────────────────
    # bulk_update writes the model directly, so the serializer's guard above does
    # not cover it. These two columns became mass-updatable when the registry
    # started deriving from the model; without the gate in views.bulk_update a
    # non-MR user could mass-write a column stripped from their own reads.

    def test_bulk_update_refuses_an_mr_field_for_other_roles(self):
        self.client.force_authenticate(user=self.user)
        for f in self.MR_FIELDS:
            with self.subTest(field=f):
                r = self.client.post(
                    f"{self.LIST}bulk_update/",
                    {"ids": [self.row.id], "field": f, "value": "sneaky",
                     "commit": False},
                    format="json")
                self.assertEqual(r.status_code, 400, r.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.internal_footnotes_mr, "Weak on delivery")

    def test_bulk_update_schema_hides_the_mr_fields_from_other_roles(self):
        self.client.force_authenticate(user=self.user)
        fields = self.client.get(f"{self.LIST}bulk_update_schema/").data["fields"]
        for f in self.MR_FIELDS:
            self.assertNotIn(f, fields)

    def test_mr_can_bulk_update_the_fields(self):
        self.client.force_authenticate(user=self.mr_user)
        fields = self.client.get(f"{self.LIST}bulk_update_schema/").data["fields"]
        for f in self.MR_FIELDS:
            self.assertIn(f, fields)

        preview = self.client.post(
            f"{self.LIST}bulk_update/",
            {"ids": [self.row.id], "field": "internal_footnotes_mr",
             "value": "Batch reassessed", "commit": False}, format="json")
        self.assertEqual(preview.status_code, 200, preview.content)
        commit = self.client.post(
            f"{self.LIST}bulk_update/",
            {"ids": [self.row.id], "field": "internal_footnotes_mr",
             "value": "Batch reassessed", "commit": True,
             "plan_hash": preview.data["plan_hash"]}, format="json")
        self.assertEqual(commit.status_code, 200, commit.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.internal_footnotes_mr, "Batch reassessed")

    def test_a_blank_echo_does_not_wipe_the_stored_value(self):
        """
        The shared form posts all 21 keys. A non-MR user editing another column
        must not blank MR notes they cannot even see.
        """
        self.client.force_authenticate(user=self.user)
        r = self.client.patch(
            f"{self.LIST}{self.row.id}/",
            {"company_name": "New Co", "internal_footnotes_mr": "",
             "slot_recommendation_mr": ""},
            format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.company_name, "New Co")
        self.assertEqual(self.row.internal_footnotes_mr, "Weak on delivery")
        self.assertEqual(self.row.slot_recommendation_mr, "Put on the main stage")

    def test_linkedin_company_stays_visible_to_everyone(self):
        """Absent from the Zoho quickview, but a public URL — not confidential."""
        self.client.force_authenticate(user=self.user)
        r = self.client.get(f"{self.LIST}{self.row.id}/")
        self.assertIn("linkedin_company", r.data)


class MixinWiringTests(_Base):
    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_filter_schema_endpoint_works(self):
        r = self.client.get(f"{self.LIST}filter_schema/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["match_modes"], ["all"])
        for f in ("event_code", "qc_grade", "speaker_slot_status"):
            self.assertIn(f, r.data["fields"])
        # Audit FKs are excluded from the registry.
        self.assertNotIn("created_by", r.data["fields"])

    def test_filter_spec_actually_filters(self):
        import json
        from urllib.parse import quote
        ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Keep Me", email="k@x.com",
            qc_grade="A", submission_date=date(2026, 2, 2))
        ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Drop Me", email="d@x.com",
            qc_grade="C", submission_date=date(2026, 2, 3))
        spec = {"match": "all",
                "criteria": [{"field": "qc_grade", "op": "is", "value": "A"}]}
        r = self.client.get(f"{self.LIST}?filter_spec={quote(json.dumps(spec))}")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["speaker_name"], "Keep Me")

    def test_bulk_update_schema_covers_the_outcome_fields_and_no_identity(self):
        """
        The registry is derived from the model now, so every editable column
        comes with it. What must NOT be there is the whole safety argument, and
        is asserted explicitly.
        """
        r = self.client.get(f"{self.LIST}bulk_update_schema/")
        self.assertEqual(r.status_code, 200, r.content)
        required = {
            "qc_grade", "qc_score", "speaker_slot_status", "sponsorship_status",
            "agenda_slot", "revenue_possibility", "sales_pitch_factor",
            "agenda_addition", "spex_remarks",
        }
        wired = set(r.data["fields"])
        self.assertTrue(required <= wired, required - wired)
        # Identity, provenance and audit must never be mass-writable.
        for forbidden in ("event_code", "speaker_name", "email", "company_name",
                          "source_paper_review", "import_batch_id",
                          "created_by", "updated_by", "id"):
            self.assertNotIn(forbidden, wired)

    def test_qc_score_keeps_its_floor_from_the_model_validator(self):
        """
        The >= 0 rule is MinValueValidator(0) on the column; the registry reads
        it rather than restating it, so removing the validator would remove the
        bound in one place instead of leaving the two disagreeing.
        """
        r = self.client.get(f"{self.LIST}bulk_update_schema/")
        self.assertEqual(r.data["fields"]["qc_score"]["min"], 0)
        self.assertTrue(r.data["fields"]["qc_score"]["nullable"])

    def test_bulk_update_preview_writes_nothing(self):
        rows = [ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name=f"P{i}", email=f"p{i}@x.com",
            qc_grade="C") for i in range(3)]
        ids = [r.id for r in rows]
        r = self.client.post(f"{self.LIST}bulk_update/",
                             {"ids": ids, "field": "qc_grade", "commit": False},
                             format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["updated"], 0)
        self.assertEqual(r.data["permitted"], 3)
        self.assertTrue(r.data["plan_hash"])

    def test_bulk_update_commits_and_logs_one_entry_with_every_id(self):
        rows = [ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name=f"B{i}", email=f"b{i}@x.com",
            qc_grade="C") for i in range(4)]
        ids = [r.id for r in rows]

        preview = self.client.post(
            f"{self.LIST}bulk_update/",
            {"ids": ids, "field": "qc_grade", "value": "A", "commit": False},
            format="json")
        self.assertEqual(preview.status_code, 200, preview.content)
        logs_before = ActionLog.objects.count()

        r = self.client.post(
            f"{self.LIST}bulk_update/",
            {"ids": ids, "field": "qc_grade", "value": "A", "commit": True,
             "plan_hash": preview.data["plan_hash"]},
            format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["updated"], 4)
        for row in rows:
            row.refresh_from_db()
            self.assertEqual(row.qc_grade, "A")

        # Exactly one log line for the batch, carrying every id untruncated.
        self.assertEqual(ActionLog.objects.count(), logs_before + 1)
        log = ActionLog.objects.latest("id")
        for i in ids:
            self.assertIn(str(i), log.details)

    def test_bulk_update_rejects_a_value_outside_the_choice_list(self):
        row = ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="X", email="x@x.com")
        r = self.client.post(
            f"{self.LIST}bulk_update/",
            {"ids": [row.id], "field": "qc_grade", "value": "Z", "commit": False},
            format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_bulk_update_qc_score_coerces_and_rejects_non_numeric(self):
        """
        qc_score is an IntegerField. Garbage must come back as a 400, not blow up
        inside save() as a 500 — that is what the local "integer" type is for.
        """
        row = ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="N", email="n@x.com")

        bad = self.client.post(
            f"{self.LIST}bulk_update/",
            {"ids": [row.id], "field": "qc_score", "value": "abc", "commit": False},
            format="json")
        self.assertEqual(bad.status_code, 400, bad.content)

        neg = self.client.post(
            f"{self.LIST}bulk_update/",
            {"ids": [row.id], "field": "qc_score", "value": "-5", "commit": False},
            format="json")
        self.assertEqual(neg.status_code, 400, neg.content)

        preview = self.client.post(
            f"{self.LIST}bulk_update/",
            {"ids": [row.id], "field": "qc_score", "value": "31", "commit": False},
            format="json")
        self.assertEqual(preview.status_code, 200, preview.content)
        ok = self.client.post(
            f"{self.LIST}bulk_update/",
            {"ids": [row.id], "field": "qc_score", "value": "31", "commit": True,
             "plan_hash": preview.data["plan_hash"]},
            format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        row.refresh_from_db()
        self.assertEqual(row.qc_score, 31)

    def test_stable_ordering_filter_is_on_the_viewset(self):
        from accounts.ordering import StableOrderingFilter
        from proposal_submission.views import ProposalSubmissionViewSet
        self.assertIn(StableOrderingFilter,
                      ProposalSubmissionViewSet.filter_backends)


class DuplicateActionTests(_Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth import get_user_model
        U = get_user_model()
        cls.dup_user = U.objects.create_user(
            username="dup_admin", password="x", email="dup@x.com",
            role="admin", team=cls.role,
        )
        cls.source = ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Original Speaker",
            email="orig@x.com", company_name="Orig Co",
            submission_date=date(2026, 6, 6), participation_type="Speaker",
            qc_grade="B", qc_score=27, presentation_theme="theme",
            linkedin_speaker="https://www.linkedin.com/in/x/",
            linkedin_followers=417, agenda_slot="Day 1",
            revenue_possibility="High", spex_remarks="remarks",
            sales_pitch_factor="factor", agenda_addition="addition",
            internal_footnotes_mr="notes", slot_recommendation_mr="rec",
            speaker_slot_status="Confirmed", sponsorship_status="Pending",
        )

    def setUp(self):
        self.client.force_authenticate(user=self.dup_user)

    def test_duplicate_copies_every_business_field_to_a_new_row(self):
        from proposal_submission.views import BUSINESS_FIELDS
        before = ProposalSubmission.objects.count()
        r = self.client.post(f"{self.LIST}{self.source.id}/duplicate/")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(ProposalSubmission.objects.count(), before + 1)

        clone = ProposalSubmission.objects.get(id=r.data["id"])
        self.assertNotEqual(clone.id, self.source.id)
        for f in BUSINESS_FIELDS:
            with self.subTest(field=f):
                self.assertEqual(getattr(clone, f), getattr(self.source, f))

    def test_duplicate_reassigns_created_by_and_clears_updated_by(self):
        r = self.client.post(f"{self.LIST}{self.source.id}/duplicate/")
        clone = ProposalSubmission.objects.get(id=r.data["id"])
        self.assertEqual(clone.created_by.username, "dup_admin")
        self.assertIsNone(clone.updated_by)

    def test_duplicate_copies_qc_grade_and_score(self):
        """ASSUMPTION under test — see the docstring on the duplicate action."""
        r = self.client.post(f"{self.LIST}{self.source.id}/duplicate/")
        clone = ProposalSubmission.objects.get(id=r.data["id"])
        self.assertEqual(clone.qc_grade, "B")
        self.assertEqual(clone.qc_score, 27)

    def test_duplicate_is_logged(self):
        before = ActionLog.objects.count()
        r = self.client.post(f"{self.LIST}{self.source.id}/duplicate/")
        self.assertEqual(ActionLog.objects.count(), before + 1)
        log = ActionLog.objects.latest("id")
        self.assertIn("Duplicated proposal submission", log.action)
        self.assertIn(str(self.source.id), log.action)
        self.assertIn(str(r.data["id"]), log.action)

    def test_duplicate_denied_without_the_module(self):
        self.client.force_authenticate(user=self.blind_user)
        r = self.client.post(f"{self.LIST}{self.source.id}/duplicate/")
        self.assertEqual(r.status_code, 403)

    def test_duplicate_of_a_missing_row_is_404(self):
        r = self.client.post(f"{self.LIST}999999/duplicate/")
        self.assertEqual(r.status_code, 404)


class SerializerContractTests(_Base):
    """
    Guard against shipping a serializer that silently omits a model field —
    that has happened before in this codebase.
    """

    def test_serializer_declares_every_non_system_model_field(self):
        from proposal_submission.serializers import ProposalSubmissionSerializer
        declared = set(ProposalSubmissionSerializer().get_fields())
        missing = model_field_names() - declared
        self.assertEqual(
            missing, set(),
            f"serializer is missing model field(s): {sorted(missing)}")

    def test_bulk_update_whitelist_only_names_real_model_fields(self):
        from proposal_submission.views import ProposalSubmissionViewSet
        every = model_field_names(exclude_system=False)
        for field in ProposalSubmissionViewSet.bulk_update_fields:
            with self.subTest(field=field):
                self.assertIn(field, every)

    def test_business_fields_constant_matches_the_model(self):
        """BUSINESS_FIELDS drives duplicate(); drift would silently skip a column."""
        from proposal_submission.views import BUSINESS_FIELDS
        self.assertEqual(set(BUSINESS_FIELDS), model_field_names())
