"""
paper_review/tests_import.py
─────────────────────────────
PART B — the two-phase import.

B2 is the highest-consequence property in the module and gets the most rigour:
an import must fire NEITHER workflow. The suppression tests force
PAPER_REVIEW_NOTIFICATIONS_ENABLED=True before asserting mail.outbox is empty,
because the flag defaults False — asserting suppression with the kill switch on
would prove nothing about the import itself, only that the kill switch works
(which tests_notifications_disabled.py already proves separately).
"""
from datetime import date

from django.core import mail
from django.contrib.auth import get_user_model
from django.test import override_settings

from accounts.models import ActionLog
from paper_review.importer import (
    FIELD_TO_LABEL, ZOHO_HEADERS, computed_score, map_headers,
)
from paper_review.models import NotificationLog, PaperReview
from paper_review.tests import ALERT, LOCMEM, _Base, make_event
from proposal_submission.models import ProposalSubmission

U = get_user_model()


# ══ B3. HEADER MAPPING ═══════════════════════════════════════════════════════

class HeaderMappingTests(_Base):
    """
    Every label B3 names, including the ones that are not slugs of their label:
    the LinkedIn columns, the six criteria carrying their maximum in parentheses,
    the comma-bearing and apostrophe-bearing ones, and the trailing-space case.
    """

    CASES = [
        ("Event Code",                            "event_code"),
        ("Speaker Name",                          "speaker_name"),
        ("Company Name",                          "company_name"),
        ("Email Address of the Speaker",          "email"),
        ("LinkedIn Profile of Speaker",           "linkedin_speaker"),
        ("LinkedIn Followers Count of Speaker",   "linkedin_followers"),
        ("LinkedIn Company Profile",              "linkedin_company"),
        ("Closeness to Topic (10) ",              "closeness_to_topic"),
        ("Closeness to Region (5)",               "closeness_to_region"),
        ("Clear Solution to Challenges (10)",     "clear_solution_to_challenges"),
        ("Case Study, Results, Examples (5)",     "case_study_results_examples"),
        ("Not an obvious 'Sales Pitch' (5)",      "not_obvious_sales_pitch"),
        ("Company Profile (10)",                  "company_profile_score"),
        ("Session or Location on Agenda",         "session_location_on_agenda"),
        ("Feedback to Speaker or Request Information", "feedback_to_speaker"),
        ("NOS?",                                  "nos"),
        ("Theme",                                 "theme"),
        ("Internal Footnotes",                    "internal_footnotes"),
        ("Proposal Score",                        "proposal_score"),
        ("Proposal Received",                     "proposal_received"),
        ("Grade",                                 "grade"),
        ("Paper Submission Date",                 "paper_submission_date"),
        ("Agenda Addition",                       "agenda_addition"),
        ("Speaker Email Ref",                     "speaker_email_ref"),
        ("Research Email Ref",                    "research_email_ref"),
        ("Added User",                            "created_by"),
        ("Added Time",                            "created_at"),
    ]

    def test_every_zoho_label_maps(self):
        mapping, unknown = map_headers([label for label, _ in self.CASES])
        self.assertEqual(unknown, [])
        for label, field in self.CASES:
            with self.subTest(label=label):
                self.assertEqual(mapping[label], field)

    def test_the_trailing_space_case_specifically(self):
        """"Closeness to Topic (10) " — trailing space, as exported."""
        mapping, unknown = map_headers(["Closeness to Topic (10) "])
        self.assertEqual(unknown, [])
        self.assertEqual(mapping["Closeness to Topic (10) "], "closeness_to_topic")

    def test_mapping_is_case_and_whitespace_insensitive(self):
        mapping, unknown = map_headers(
            ["  eVeNt   CoDe ", "EMAIL ADDRESS OF THE SPEAKER", "nos?"])
        self.assertEqual(unknown, [])
        self.assertEqual(sorted(mapping.values()), ["email", "event_code", "nos"])

    def test_model_field_names_are_accepted_too(self):
        mapping, unknown = map_headers(
            ["event_code", "speaker_name", "closeness_to_topic"])
        self.assertEqual(unknown, [])
        self.assertEqual(len(mapping), 3)

    def test_unrecognised_columns_are_reported_never_dropped_silently(self):
        mapping, unknown = map_headers(["Event Code", "Mystery Column", "Notes?"])
        self.assertEqual(unknown, ["Mystery Column", "Notes?"])

    def test_every_mapped_field_has_an_export_label(self):
        """
        C3 round-trips through this map, so a field the importer accepts but the
        exporter cannot label would break the round trip in one direction only.
        """
        from paper_review.importer import AUDIT_COLUMNS
        for field in set(ZOHO_HEADERS.values()) - set(AUDIT_COLUMNS):
            with self.subTest(field=field):
                self.assertIn(field, FIELD_TO_LABEL)


class ImportBase(_Base):
    PREVIEW = "/api/paper-reviews/import/preview/"
    COMMIT  = "/api/paper-reviews/import/commit/"

    def setUp(self):
        # The preview/commit helpers authenticate per call, but create_review()
        # (inherited from _Base, used by the suppression control test) posts
        # directly and needs an authenticated client.
        self.client.force_authenticate(user=self.user)

    def row(self, **over):
        base = {
            "Event Code": "AFS - JS",
            "Paper Submission Date": "2026-08-10",
            "Speaker Name": "Import One",
            "Company Name": "Cicada Logistics",
            "Email Address of the Speaker": "import.one@example.com",
            "LinkedIn Profile of Speaker": "https://linkedin.com/in/import-one",
            "LinkedIn Followers Count of Speaker": 417,
            "LinkedIn Company Profile": "https://linkedin.com/company/cicada",
            "NOS?": "Yes",
            "Closeness to Topic (10)": 9,
            "Closeness to Region (5)": 2,
            "Clear Solution to Challenges (10)": 9,
            "Case Study, Results, Examples (5)": 1,
            "Not an obvious 'Sales Pitch' (5)": 1,
            "Company Profile (10)": 5,
            "Proposal Score": 27,
            "Grade": "B",
            "Session or Location on Agenda": "Day 1, Afternoon Session",
            "Theme": "terminal and rail environment",
            "Proposal Received": "Terminal decarbonisation",
            "Agenda Addition": "CHALLENGES",
            "Feedback to Speaker or Request Information": "Add a case study.",
        }
        base.update(over)
        return base

    def preview(self, rows, user=None, import_batch_id=None):
        self.client.force_authenticate(user=user or self.user)
        body = {"rows": rows}
        if import_batch_id is not None:
            body["import_batch_id"] = str(import_batch_id)
        return self.client.post(self.PREVIEW, body, format="json")

    def commit(self, rows, plan_hash, user=None, filename="zoho.xlsx",
              import_batch_id=None):
        import uuid as _uuid
        self.client.force_authenticate(user=user or self.user)
        return self.client.post(self.COMMIT, {
            "rows": rows, "plan_hash": plan_hash, "filename": filename,
            "import_batch_id": str(import_batch_id or _uuid.uuid4()),
        }, format="json")

    def import_rows(self, rows, user=None):
        """preview → commit with the returned hash and batch id, in one call."""
        p = self.preview(rows, user=user)
        self.assertEqual(p.status_code, 200, p.content)
        return self.commit(rows, p.data["plan_hash"], user=user,
                          import_batch_id=p.data["import_batch_id"])


# ══ B2. IMPORT MUST FIRE NEITHER WORKFLOW ════════════════════════════════════

@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL="crm@example.com",
                   PAPER_REVIEW_ALERT_EMAIL=ALERT,
                   # FORCED ON. The flag defaults False, so asserting an empty
                   # outbox with it off would prove nothing about the import.
                   PAPER_REVIEW_NOTIFICATIONS_ENABLED=True)
class WorkflowSuppressionTests(ImportBase):
    """
    THE highest-consequence property: a 400-row historical import must not send
    400 emails and must not mint 400 proposal submissions.
    """

    def test_the_flag_really_is_on_for_this_class(self):
        """
        Guards the guard. If this override ever stops applying, every other test
        in this class would pass for the wrong reason.
        """
        from django.conf import settings
        self.assertTrue(settings.PAPER_REVIEW_NOTIFICATIONS_ENABLED)

    def test_a_form_create_does_send_so_the_comparison_is_meaningful(self):
        """
        The control. With the flag forced ON, the ordinary create path DOES send
        and DOES mint a proposal — so an empty outbox after an import is the
        import's doing, not a dead notification path.
        """
        r = self.create_review()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(ProposalSubmission.objects.count(), 1)

    def test_an_import_sends_no_email(self):
        rows = [self.row(**{"Email Address of the Speaker": f"b{i}@example.com"})
                for i in range(12)]
        with self.captureOnCommitCallbacks(execute=True):
            r = self.import_rows(rows)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["created"], 12)
        self.assertEqual(len(mail.outbox), 0,
                         "an import must never notify the production team")

    def test_an_import_creates_no_proposal_submissions(self):
        rows = [self.row(**{"Email Address of the Speaker": f"c{i}@example.com"})
                for i in range(12)]
        with self.captureOnCommitCallbacks(execute=True):
            r = self.import_rows(rows)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PaperReview.objects.count(), 12)
        self.assertEqual(ProposalSubmission.objects.count(), 0,
                         "an import must never generate proposal submissions")

    def test_an_import_writes_no_notification_log_rows_either(self):
        """
        Not merely "no email sent" — no notification was even ATTEMPTED, so there
        is nothing in the log. A suppressed-but-attempted send would leave a
        SUPPRESSED row; an import must leave none.
        """
        with self.captureOnCommitCallbacks(execute=True):
            self.import_rows([self.row()])
        self.assertEqual(NotificationLog.objects.count(), 0)

    def test_preview_states_the_suppression(self):
        p = self.preview([self.row()])
        block = p.data["workflows_suppressed"]
        self.assertTrue(block["proposal_submission"])
        self.assertTrue(block["production_team_email"])
        self.assertIn("does not", block["detail"].lower())

    def test_commit_restates_the_suppression(self):
        r = self.import_rows([self.row()])
        self.assertTrue(r.data["workflows_suppressed"]["proposal_submission"])
        self.assertTrue(r.data["workflows_suppressed"]["production_team_email"])

    def test_a_larger_batch_still_suppresses_both(self):
        """The 400-row scenario in miniature — scaled down for suite runtime."""
        rows = [self.row(**{"Email Address of the Speaker": f"bulk{i}@example.com"})
                for i in range(60)]
        with self.captureOnCommitCallbacks(execute=True):
            r = self.import_rows(rows)
        self.assertEqual(r.data["created"], 60)
        self.assertEqual(PaperReview.objects.count(), 60)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(ProposalSubmission.objects.count(), 0)
        self.assertEqual(NotificationLog.objects.count(), 0)


# ══ B1. PREVIEW / COMMIT CONTRACT ════════════════════════════════════════════

class ImportPreviewTests(ImportBase):
    def test_preview_writes_nothing_and_returns_a_plan(self):
        before = PaperReview.objects.count()
        r = self.preview([self.row()])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(PaperReview.objects.count(), before)
        self.assertTrue(r.data["plan_hash"])
        self.assertTrue(r.data["import_batch_id"])
        self.assertEqual(r.data["counts"]["CREATE"], 1)
        self.assertEqual(r.data["rows"][0]["row"], 1)
        # Internal payload never leaks to the client.
        self.assertNotIn("_payload", r.data["rows"][0])

    def test_row_cap_is_named_in_the_error(self):
        rows = [self.row(**{"Email Address of the Speaker": f"a{i}@x.com"})
                for i in range(501)]
        r = self.preview(rows)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("500", str(r.data["rows"]))

    def test_exactly_the_cap_is_accepted(self):
        rows = [self.row(**{"Email Address of the Speaker": f"b{i}@x.com"})
                for i in range(500)]
        r = self.preview(rows)
        self.assertEqual(r.status_code, 200, r.content)

    def test_unrecognised_columns_surface_in_the_preview(self):
        r = self.preview([self.row(**{"Wat": "x"})])
        self.assertEqual(r.data["unrecognised_columns"], ["Wat"])

    def test_audit_columns_are_reported_as_ignored_not_unrecognised(self):
        """
        "Added User" / "Added Time" are recognised so they do not read as typos,
        but deliberately not written — and the preview says so rather than letting
        the importer believe authorship was preserved.
        """
        r = self.preview([self.row(**{"Added User": "Someone",
                                      "Added Time": "2023-01-01"})])
        self.assertEqual(r.data["unrecognised_columns"], [])
        self.assertTrue(r.data["ignored_columns"])

    def test_no_recognisable_columns_is_400(self):
        r = self.preview([{"Nope": 1, "Also Nope": 2}])
        self.assertEqual(r.status_code, 400, r.content)

    def test_malformed_bodies(self):
        self.client.force_authenticate(user=self.user)
        for body in ({"rows": []}, {"rows": "nope"}, {}, {"rows": [1, 2]}):
            with self.subTest(body=body):
                r = self.client.post(self.PREVIEW, body, format="json")
                self.assertEqual(r.status_code, 400)

    def test_preview_denied_without_the_module(self):
        r = self.preview([self.row()], user=self.blind_user)
        self.assertEqual(r.status_code, 403)


class ImportCommitTests(ImportBase):
    def test_commit_writes_and_skips_error_rows(self):
        rows = [
            self.row(**{"Email Address of the Speaker": "good@example.com"}),
            self.row(**{"Event Code": "NO-SUCH-CODE",
                        "Email Address of the Speaker": "bad@example.com"}),
        ]
        r = self.import_rows(rows)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["created"], 1)
        self.assertEqual(r.data["skipped"], 1)
        created = PaperReview.objects.get(id=r.data["created_ids"][0])
        self.assertEqual(created.email, "good@example.com")
        self.assertEqual(created.created_by, self.user)

    def test_stale_hash_is_409_and_writes_nothing(self):
        rows = [self.row()]
        before = PaperReview.objects.count()
        r = self.commit(rows, "deadbeef")
        self.assertEqual(r.status_code, 409, r.content)
        self.assertFalse(r.data["success"])
        self.assertTrue(r.data["plan_hash"])
        self.assertEqual(PaperReview.objects.count(), before,
                         "never a partial write on a stale hash")

    def test_commit_requires_both_the_hash_and_the_batch_id(self):
        rows = [self.row()]
        p = self.preview(rows)
        self.client.force_authenticate(user=self.user)

        no_hash = self.client.post(self.COMMIT, {
            "rows": rows, "import_batch_id": p.data["import_batch_id"]},
            format="json")
        self.assertEqual(no_hash.status_code, 400, no_hash.content)
        self.assertIn("plan_hash", no_hash.data)

        no_batch = self.client.post(self.COMMIT, {
            "rows": rows, "plan_hash": p.data["plan_hash"]}, format="json")
        self.assertEqual(no_batch.status_code, 400, no_batch.content)
        self.assertIn("import_batch_id", no_batch.data)

    def test_every_row_is_stamped_with_the_batch_id(self):
        rows = [self.row(**{"Email Address of the Speaker": f"s{i}@example.com"})
                for i in range(3)]
        p = self.preview(rows)
        batch = p.data["import_batch_id"]
        r = self.commit(rows, p.data["plan_hash"], import_batch_id=batch)
        for pk in r.data["created_ids"]:
            self.assertEqual(str(PaperReview.objects.get(id=pk).import_batch_id),
                             batch)

    def test_a_form_created_review_has_no_batch_id(self):
        rid = self.create_review().data["id"]
        self.assertIsNone(PaperReview.objects.get(id=rid).import_batch_id)

    # ── B8 ────────────────────────────────────────────────────────────────────

    def test_one_actionlog_per_batch_with_everything_in_details(self):
        rows = [self.row(**{"Email Address of the Speaker": f"log{i}@example.com"})
                for i in range(6)]
        p = self.preview(rows)
        before = ActionLog.objects.count()
        r = self.commit(rows, p.data["plan_hash"], filename="zoho-history.xlsx",
                       import_batch_id=p.data["import_batch_id"])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(ActionLog.objects.count(), before + 1)

        log = ActionLog.objects.latest("id")
        self.assertIn("Imported 6 paper reviews", log.action)
        self.assertIn("zoho-history.xlsx", log.details)
        self.assertIn(p.data["import_batch_id"], log.details)
        # COMPLETE id list, not truncated.
        for pk in r.data["created_ids"]:
            self.assertIn(str(pk), log.details)

    def test_commit_denied_without_the_module(self):
        rows = [self.row()]
        p = self.preview(rows)
        r = self.commit(rows, p.data["plan_hash"], user=self.blind_user,
                       import_batch_id=p.data["import_batch_id"])
        self.assertEqual(r.status_code, 403)


# ══ B4 / B5. NULL CRITERIA AND SCORE RECONCILIATION ══════════════════════════

class ScoreHandlingTests(ImportBase):
    def test_computed_score_excludes_nulls(self):
        self.assertEqual(computed_score({"a": 8, "b": None, "c": 3}), 11)
        self.assertIsNone(computed_score({"a": None, "b": None}))

    def test_an_imported_row_may_carry_null_criteria(self):
        """
        B4 — the import bypasses the serializer's REQUIRED_FIELDS deliberately,
        which is the whole reason the model is nullable.
        """
        row = self.row()
        for label in ("Closeness to Region (5)", "Company Profile (10)"):
            row[label] = ""
        r = self.import_rows([row])
        self.assertEqual(r.status_code, 201, r.content)
        review = PaperReview.objects.get(id=r.data["created_ids"][0])
        self.assertIsNone(review.closeness_to_region)
        self.assertIsNone(review.company_profile_score)
        # 9 + 9 + 1 + 1, the four that were present.
        self.assertEqual(review.proposal_score, 20)

    def test_all_criteria_blank_leaves_the_score_null(self):
        row = self.row()
        for label, field in (
            ("Closeness to Topic (10)", None), ("Closeness to Region (5)", None),
            ("Clear Solution to Challenges (10)", None),
            ("Case Study, Results, Examples (5)", None),
            ("Not an obvious 'Sales Pitch' (5)", None),
            ("Company Profile (10)", None),
        ):
            row[label] = ""
        row["Proposal Score"] = ""
        r = self.import_rows([row])
        review = PaperReview.objects.get(id=r.data["created_ids"][0])
        self.assertIsNone(review.proposal_score,
                          "unscored must read as unscored, not 0/45")

    def test_a_score_mismatch_warns_naming_both_numbers(self):
        """B5 — never silently trust the file, never silently overwrite it."""
        row = self.row(**{"Proposal Score": 40})     # criteria sum to 27
        p = self.preview([row])
        entry = p.data["rows"][0]
        self.assertEqual(entry["classification"], "CREATE_WITH_WARNING")
        self.assertIn("40", entry["warning"])
        self.assertIn("27", entry["warning"])

    def test_the_computed_value_is_the_one_imported(self):
        row = self.row(**{"Proposal Score": 40})
        r = self.import_rows([row])
        review = PaperReview.objects.get(id=r.data["created_ids"][0])
        self.assertEqual(review.proposal_score, 27,
                         "the computed value wins over the file's")

    def test_a_matching_score_is_a_plain_create(self):
        p = self.preview([self.row(**{"Proposal Score": 27})])
        self.assertEqual(p.data["rows"][0]["classification"], "CREATE")
        self.assertNotIn("warning", p.data["rows"][0])

    def test_grade_imports_as_recorded_and_is_never_derived(self):
        """
        B5 — grade is manual, so what MR recorded stands. 27/45 is 60%, which the
        form's own bands would call "B"; importing "D" must store "D".
        """
        r = self.import_rows([self.row(**{"Grade": "D"})])
        review = PaperReview.objects.get(id=r.data["created_ids"][0])
        self.assertEqual(review.grade, "D")
        self.assertEqual(review.proposal_score, 27)


# ══ B6. CLASSIFICATION ═══════════════════════════════════════════════════════

class ClassificationTests(ImportBase):
    def test_missing_required_fields_are_errors_naming_the_column(self):
        cases = [
            ("Event Code", "Event Code"),
            ("Speaker Name", "Speaker Name"),
            ("Email Address of the Speaker", "Email Address of the Speaker"),
        ]
        for label, expected_field in cases:
            with self.subTest(missing=label):
                p = self.preview([self.row(**{label: ""})])
                entry = p.data["rows"][0]
                self.assertEqual(entry["classification"], "ERROR")
                self.assertIn(expected_field,
                              [e["field"] for e in entry["errors"]])

    def test_an_unresolved_event_code_is_an_error_quoting_the_raw_value(self):
        p = self.preview([self.row(**{"Event Code": "NOPE-NOT-A-CODE"})])
        entry = p.data["rows"][0]
        self.assertEqual(entry["classification"], "ERROR")
        error = next(e for e in entry["errors"] if e["field"] == "Event Code")
        self.assertEqual(error["value"], "NOPE-NOT-A-CODE")

    def test_an_out_of_scope_event_code_is_an_error(self):
        make_event("OUTSIDE - XX")
        p = self.preview([self.row(**{"Event Code": "OUTSIDE - XX"})])
        entry = p.data["rows"][0]
        self.assertEqual(entry["classification"], "ERROR")
        self.assertIn("not assigned",
                      " ".join(e["problem"] for e in entry["errors"]))

    def test_a_spacing_variant_event_code_resolves_rather_than_erroring(self):
        """C2's normalizer, exercised through the importer."""
        p = self.preview([self.row(**{"Event Code": "afs-js"})])
        entry = p.data["rows"][0]
        self.assertEqual(entry["classification"], "CREATE", entry["errors"])
        self.assertEqual(entry["event_code"], "AFS - JS")

    def test_an_unparseable_date_is_an_error_quoting_the_raw_value(self):
        p = self.preview([self.row(**{"Paper Submission Date": "not a date"})])
        entry = p.data["rows"][0]
        self.assertEqual(entry["classification"], "ERROR")
        error = next(e for e in entry["errors"]
                     if e["field"] == "Paper Submission Date")
        self.assertIn("not a date", error["value"])

    def test_the_dirty_date_variants_import_cleanly(self):
        """The C1 back-port, exercised through this importer too."""
        for raw, expected in [("20 - Dec - 2025", date(2025, 12, 20)),
                              ("21-February -2026", date(2026, 2, 21)),
                              ("8 Jan 2026", date(2026, 1, 8))]:
            with self.subTest(raw=raw):
                r = self.import_rows([self.row(**{
                    "Paper Submission Date": raw,
                    "Email Address of the Speaker": f"{expected}@example.com",
                })])
                self.assertEqual(r.status_code, 201, r.content)
                review = PaperReview.objects.get(id=r.data["created_ids"][0])
                self.assertEqual(review.paper_submission_date, expected)

    def test_a_criterion_above_its_max_is_an_error_naming_the_bound(self):
        p = self.preview([self.row(**{"Closeness to Region (5)": 6})])
        entry = p.data["rows"][0]
        self.assertEqual(entry["classification"], "ERROR")
        error = next(e for e in entry["errors"]
                     if e["field"] == "Closeness to Region (5)")
        self.assertIn("between 0 and 5", error["problem"])

    def test_each_criterion_accepts_its_own_maximum(self):
        from paper_review.models import CRITERIA
        for field, maximum in CRITERIA:
            with self.subTest(field=field):
                p = self.preview([self.row(**{FIELD_TO_LABEL[field]: maximum})])
                self.assertEqual(p.data["rows"][0]["classification"],
                                 "CREATE_WITH_WARNING",
                                 "score now disagrees with the file's 27, so a "
                                 "warning is correct — but not an ERROR")

    def test_a_duplicate_against_stored_data_warns(self):
        self.import_rows([self.row()])
        p = self.preview([self.row()])
        entry = p.data["rows"][0]
        self.assertEqual(entry["classification"], "CREATE_WITH_WARNING")
        self.assertIn("already exists", entry["warning"])

    def test_a_file_duplicating_itself_warns_on_the_second_row(self):
        p = self.preview([self.row(), self.row()])
        self.assertEqual(p.data["rows"][0]["classification"], "CREATE")
        self.assertEqual(p.data["rows"][1]["classification"],
                         "CREATE_WITH_WARNING")
        self.assertIn("this file already contains",
                      p.data["rows"][1]["warning"].lower())

    def test_counts_are_returned_per_category(self):
        rows = [
            self.row(**{"Email Address of the Speaker": "one@example.com"}),
            self.row(**{"Email Address of the Speaker": "two@example.com",
                        "Proposal Score": 99}),
            self.row(**{"Event Code": "", "Email Address of the Speaker": "x@e.com"}),
        ]
        p = self.preview(rows)
        self.assertEqual(p.data["counts"]["CREATE"], 1)
        self.assertEqual(p.data["counts"]["CREATE_WITH_WARNING"], 1)
        self.assertEqual(p.data["counts"]["ERROR"], 1)
        self.assertEqual(p.data["importable"], 2)

    def test_the_response_stays_readable_at_many_error_rows(self):
        """
        The Zoho migration had 86 of 215 codes fail to match. A preview of ~130
        ERROR rows must still be one readable response rather than a timeout or a
        truncated body.
        """
        rows = [self.row(**{"Event Code": f"GHOST-{i}",
                            "Email Address of the Speaker": f"g{i}@example.com"})
                for i in range(130)]
        p = self.preview(rows)
        self.assertEqual(p.status_code, 200, p.content)
        self.assertEqual(p.data["counts"]["ERROR"], 130)
        self.assertEqual(p.data["importable"], 0)
        self.assertEqual(len(p.data["rows"]), 130)
        # Every row still carries its own reason.
        self.assertTrue(all(r["errors"] for r in p.data["rows"]))

    def test_nos_accepts_the_spreadsheet_boolean_spellings(self):
        for raw, expected in [("Yes", True), ("No", False), ("TRUE", True),
                              ("false", False), (1, True), (0, False), ("", False)]:
            with self.subTest(raw=raw):
                r = self.import_rows([self.row(**{
                    "NOS?": raw,
                    "Email Address of the Speaker": f"nos{raw}@example.com",
                })])
                self.assertEqual(r.status_code, 201, r.content)
                review = PaperReview.objects.get(id=r.data["created_ids"][0])
                self.assertIs(review.nos, expected)


# ══ B7. MR CONTENT FROM A NON-PERMITTED USER ═════════════════════════════════

class MRImportGuardTests(ImportBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mr = U.objects.create_user(
            username="imp_mr", password="x", email="impmr@example.com",
            role="market_research", team=cls.role)
        cls.mr.assigned_events.set([cls.event])

    def test_whole_file_refusal_naming_the_column(self):
        r = self.preview([self.row(**{"Internal Footnotes": "MR only"})])
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("Internal Footnotes", str(r.data))
        self.assertIn("Internal Footnotes", r.data["columns"])

    def test_the_refusal_is_whole_file_not_per_row(self):
        """
        One offending row refuses the WHOLE file. Dropping that column per-row
        would let the importer believe the MR notes landed.
        """
        rows = [
            self.row(**{"Email Address of the Speaker": "clean@example.com"}),
            self.row(**{"Email Address of the Speaker": "dirty@example.com",
                        "Internal Footnotes": "MR only"}),
        ]
        r = self.preview(rows)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(PaperReview.objects.count(), 0)

    def test_a_blank_mr_column_is_fine(self):
        r = self.preview([self.row(**{"Internal Footnotes": ""})])
        self.assertEqual(r.status_code, 200, r.content)

    def test_an_mr_user_may_import_mr_content(self):
        rows = [self.row(**{"Internal Footnotes": "legitimate notes"})]
        p = self.preview(rows, user=self.mr)
        self.assertEqual(p.status_code, 200, p.content)
        r = self.commit(rows, p.data["plan_hash"], user=self.mr,
                       import_batch_id=p.data["import_batch_id"])
        self.assertEqual(r.status_code, 201, r.content)
        review = PaperReview.objects.get(id=r.data["created_ids"][0])
        self.assertEqual(review.internal_footnotes, "legitimate notes")

    def test_commit_is_refused_too_not_just_preview(self):
        """A caller skipping straight to commit must hit the same wall."""
        rows = [self.row(**{"Internal Footnotes": "MR only"})]
        r = self.commit(rows, "anything")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(PaperReview.objects.count(), 0)
