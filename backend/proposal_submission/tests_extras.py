"""
proposal_submission/tests_extras.py
────────────────────────────────────
Import (A), duplicate detection (B), MR write paths (C), CSV export (E),
distinct filter options (F) and the four scope-review items (G).
"""
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import ActionLog
from proposal_submission.importer import (
    excel_serial_to_date, map_headers, parse_import_date,
)
from proposal_submission.models import ProposalSubmission
from proposal_submission.serializers import business_today
from proposal_submission.tests import _Base, make_event
from teams.models import Team

U = get_user_model()


# ══ A. IMPORT ════════════════════════════════════════════════════════════════

class HeaderMappingTests(TestCase):
    def test_zoho_labels_map_including_the_three_irregular_ones(self):
        mapping, unknown = map_headers([
            "Event Code", "Email Address", "Slot Recommendation by MR",
            "Internal Footnotes (MR)", "LinkedIn (Speaker)", "SpEx Remarks",
        ])
        self.assertEqual(mapping["Email Address"], "email")
        self.assertEqual(mapping["Slot Recommendation by MR"], "slot_recommendation_mr")
        self.assertEqual(mapping["Internal Footnotes (MR)"], "internal_footnotes_mr")
        self.assertEqual(mapping["LinkedIn (Speaker)"], "linkedin_speaker")
        self.assertEqual(mapping["SpEx Remarks"], "spex_remarks")
        self.assertEqual(unknown, [])

    def test_mapping_is_case_and_whitespace_insensitive(self):
        mapping, unknown = map_headers(["  eVeNt   CoDe ", "EMAIL ADDRESS"])
        self.assertEqual(sorted(mapping.values()), ["email", "event_code"])
        self.assertEqual(unknown, [])

    def test_model_field_names_are_accepted_too(self):
        mapping, unknown = map_headers(["event_code", "speaker_name", "qc_score"])
        self.assertEqual(sorted(mapping.values()),
                         ["event_code", "qc_score", "speaker_name"])
        self.assertEqual(unknown, [])

    def test_unrecognised_columns_are_reported_never_dropped_silently(self):
        mapping, unknown = map_headers(["Event Code", "Mystery Column", "Notes?"])
        self.assertEqual(unknown, ["Mystery Column", "Notes?"])


class DateParsingTests(TestCase):
    def test_excel_serials(self):
        self.assertEqual(excel_serial_to_date(45678), date(2025, 1, 21))
        self.assertEqual(excel_serial_to_date(25569), date(1970, 1, 1))
        self.assertIsNone(excel_serial_to_date(60), "phantom 29-Feb-1900")
        self.assertIsNone(excel_serial_to_date(2026), "outside plausible window")

    def test_all_three_input_shapes(self):
        cases = [
            (date(2026, 8, 10),                     date(2026, 8, 10)),
            (datetime(2026, 8, 10, 3, 30),          date(2026, 8, 10)),
            ("2026-08-10",                          date(2026, 8, 10)),
            ("10-Aug-2026",                         date(2026, 8, 10)),
            ("10/08/2026",                          date(2026, 8, 10)),
            ("2026-08-10T00:00:00.000000000",       date(2026, 8, 10)),
            (45678,                                 date(2025, 1, 21)),
            (45678.5,                               date(2025, 1, 21)),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                parsed, err = parse_import_date(raw)
                self.assertIsNone(err)
                self.assertEqual(parsed, expected)

    def test_blank_is_allowed_and_never_becomes_today(self):
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                parsed, err = parse_import_date(raw)
                self.assertIsNone(parsed)
                self.assertIsNone(err)

    def test_unresolvable_quotes_the_raw_value(self):
        parsed, err = parse_import_date("not a date")
        self.assertIsNone(parsed)
        self.assertIn("not a date", err)

    def test_c1_dirty_date_variants_from_the_real_zoho_instance(self):
        """
        C1. Two of these four already parsed before the fix (leading-zero-free
        %d and a clean %d-%b-%Y are both already tolerant) — only the two with
        whitespace hugging the hyphen were rejected. All four are pinned here so
        the ones that already worked cannot regress silently.
        """
        cases = [
            ("10-Mar-2025",       date(2025, 3, 10)),   # clean — already worked
            ("20 - Dec - 2025",   date(2025, 12, 20)),  # spaces around hyphens
            ("21-February -2026", date(2026, 2, 21)),   # full month + rogue space
            ("8 Jan 2026",        date(2026, 1, 8)),     # no leading zero — already worked
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                parsed, err = parse_import_date(raw)
                self.assertIsNone(err, f"{raw!r} should parse, got error {err!r}")
                self.assertEqual(parsed, expected)

    def test_c1_hyphen_collapsing_does_not_disturb_iso_or_slash_dates(self):
        """
        The whitespace-around-hyphen collapse must be a no-op for every format
        that has no hyphen-adjacent whitespace to begin with.
        """
        cases = [
            ("2026-08-10",   date(2026, 8, 10)),
            ("10/08/2026",   date(2026, 8, 10)),
            ("10.08.2026",   date(2026, 8, 10)),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                parsed, err = parse_import_date(raw)
                self.assertIsNone(err)
                self.assertEqual(parsed, expected)

    def test_c1_genuinely_bad_hyphenated_text_still_fails_and_quotes_the_raw_value(self):
        parsed, err = parse_import_date("not - a - date")
        self.assertIsNone(parsed)
        self.assertIn("not - a - date", err)


class EventCodeNormalizationTests(_Base):
    """
    C2 — the importer's own event_code resolution routed through the shared
    webhooks/event_code_normalization.py, at the classify_rows() layer directly
    rather than only through the HTTP import endpoints (ImportBase, below, covers
    the end-to-end path).
    """

    def _classify(self, rows, user=None):
        from proposal_submission.importer import classify_rows, map_headers
        rows_ = [{"Event Code": c, "Speaker Name": "X",
                 "Email Address": f"{i}@example.com"} for i, c in enumerate(rows)]
        mapping, _ = map_headers(["Event Code", "Speaker Name", "Email Address"])
        return classify_rows(rows_, mapping, user or self.user, existing_pairs=set())

    def test_spacing_variants_all_resolve_to_the_canonical_stored_code(self):
        variants = ["AFS - JS", "AFS-JS", "afs-js", "AFS  -  JS", "  AFS - JS"]
        plan = self._classify(variants, user=self.admin_full_access())
        for entry, variant in zip(plan, variants):
            with self.subTest(variant=variant):
                self.assertEqual(entry["classification"], "CREATE", entry["errors"])
                self.assertEqual(entry["event_code"], "AFS - JS")

    def admin_full_access(self):
        role = Team.objects.create(name="C2 Admin", is_all_access=True)
        return U.objects.create_user(
            username="c2_admin", password="x", email="c2admin@example.com",
            role="admin", team=role)

    def test_biu_matches_biu_gs_pm_but_never_biuk_in_the_importer(self):
        """
        THE SAME anchored-boundary guarantee A2/A1 pin for paper_review, asserted
        here too — normalisation must not weaken it for the importer's own
        resolution path. _Base already provides "BIUK - PM" (cls.other_event).
        """
        admin = self.admin_full_access()
        make_event("BIU/GS - PM")

        plan = self._classify(["BIU"], user=admin)
        self.assertEqual(plan[0]["classification"], "CREATE", plan[0]["errors"])
        self.assertEqual(plan[0]["event_code"], "BIU/GS - PM")
        self.assertNotEqual(plan[0]["event_code"], "BIUK - PM")

    def test_biuk_resolves_to_biuk_only_in_the_importer(self):
        admin = self.admin_full_access()
        make_event("BIU/GS - PM")

        plan = self._classify(["BIUK"], user=admin)
        self.assertEqual(plan[0]["classification"], "CREATE", plan[0]["errors"])
        self.assertEqual(plan[0]["event_code"], "BIUK - PM")

    def test_a_spacing_only_miss_now_resolves_instead_of_erroring(self):
        """
        THE GAP THIS CLOSES: before C2, 'AFS-JS' in a spreadsheet became an ERROR
        row against a catalogue that only holds 'AFS - JS' — not even a substring
        match, so the candidate list came back empty too.
        """
        plan = self._classify(["AFS-JS"], user=self.user)
        self.assertEqual(plan[0]["classification"], "CREATE", plan[0]["errors"])
        self.assertEqual(plan[0]["event_code"], "AFS - JS")


class ImportBase(_Base):
    PREVIEW = "/api/proposal-submissions/import/preview/"
    COMMIT  = "/api/proposal-submissions/import/commit/"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mr_user = U.objects.create_user(
            username="imp_mr", password="x", email="impmr@x.com",
            role="market_research", team=cls.role)
        cls.mr_user.assigned_events.set([cls.event, cls.other_event])

    def row(self, **over):
        base = {
            "Event Code": "AFS - JS", "Speaker Name": "Import One",
            "Email Address": "import.one@example.com",
            "Submission Date": "2026-08-10", "QC Score": 27, "QC Grade": "B",
        }
        base.update(over)
        return base

    def preview(self, rows, user=None, import_batch_id=None):
        self.client.force_authenticate(user=user or self.user)
        body = {"rows": rows}
        if import_batch_id is not None:
            body["import_batch_id"] = str(import_batch_id)
        return self.client.post(self.PREVIEW, body, format="json")

    def commit(self, rows, plan_hash, user=None, filename="test.xlsx",
              import_batch_id=None):
        """
        import_batch_id defaults to a fresh uuid4 rather than None — C4 makes it
        a required field on commit (mirroring plan_hash), so every existing
        caller of this helper needs SOME valid value without having to know that
        to keep working; tests that care about the real preview->commit chain
        pass the id they got back from self.preview(...) explicitly.
        """
        import uuid as _uuid
        self.client.force_authenticate(user=user or self.user)
        return self.client.post(
            self.COMMIT,
            {"rows": rows, "plan_hash": plan_hash, "filename": filename,
             "import_batch_id": str(import_batch_id or _uuid.uuid4())},
            format="json")


class ImportPreviewTests(ImportBase):
    def test_preview_writes_nothing_and_returns_a_plan(self):
        before = ProposalSubmission.objects.count()
        r = self.preview([self.row()])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(ProposalSubmission.objects.count(), before)
        self.assertTrue(r.data["plan_hash"])
        self.assertEqual(r.data["counts"]["CREATE"], 1)
        self.assertEqual(r.data["rows"][0]["row"], 1)
        self.assertEqual(r.data["rows"][0]["classification"], "CREATE")
        # Internal payload never leaks to the client.
        self.assertNotIn("_payload", r.data["rows"][0])

    def test_row_cap_is_named_in_the_error(self):
        rows = [self.row(**{"Email Address": f"a{i}@x.com"}) for i in range(501)]
        r = self.preview(rows)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("500", str(r.data["rows"]))

    def test_exactly_the_cap_is_accepted(self):
        rows = [self.row(**{"Email Address": f"b{i}@x.com"}) for i in range(500)]
        r = self.preview(rows)
        self.assertEqual(r.status_code, 200, r.content)

    def test_unrecognised_columns_surface_in_the_preview(self):
        r = self.preview([self.row(**{"Wat": "x"})])
        self.assertEqual(r.data["unrecognised_columns"], ["Wat"])

    def test_no_recognisable_columns_is_400(self):
        r = self.preview([{"Nope": 1, "Also Nope": 2}])
        self.assertEqual(r.status_code, 400, r.content)

    def test_empty_and_malformed_bodies(self):
        self.client.force_authenticate(user=self.user)
        for body in ({"rows": []}, {"rows": "nope"}, {}, {"rows": [1, 2]}):
            with self.subTest(body=body):
                r = self.client.post(self.PREVIEW, body, format="json")
                self.assertEqual(r.status_code, 400)

    def test_error_classifications(self):
        cases = {
            "missing code":   self.row(**{"Event Code": ""}),
            "missing name":   self.row(**{"Speaker Name": ""}),
            "missing email":  self.row(**{"Email Address": ""}),
            "no match":       self.row(**{"Event Code": "ZZZZ"}),
            "bad date":       self.row(**{"Submission Date": "gibberish"}),
            "negative score": self.row(**{"QC Score": -3}),
            "float score":    self.row(**{"QC Score": 1.5}),
            "long url":       self.row(**{"LinkedIn (Speaker)": "h" * 501}),
        }
        for label, row in cases.items():
            with self.subTest(case=label):
                r = self.preview([row])
                self.assertEqual(r.status_code, 200, r.content)
                entry = r.data["rows"][0]
                self.assertEqual(entry["classification"], "ERROR", label)
                self.assertTrue(entry["errors"], label)
                # The offending raw value is echoed back.
                self.assertIn("value", entry["errors"][0])

    def test_out_of_scope_code_is_a_row_error(self):
        make_event("OTHER - XX")
        r = self.preview([self.row(**{"Event Code": "OTHER - XX"})])
        entry = r.data["rows"][0]
        self.assertEqual(entry["classification"], "ERROR")
        self.assertIn("not assigned", str(entry["errors"]))

    def test_ambiguous_code_is_a_row_error_listing_matches(self):
        make_event("BIU - PM")
        make_event("BIU - RS")
        r = self.preview([self.row(**{"Event Code": "BIU"})])
        entry = r.data["rows"][0]
        self.assertEqual(entry["classification"], "ERROR")
        self.assertIn("ambiguous", str(entry["errors"]).lower())

    def test_bookings_off_event_is_a_success_not_an_error(self):
        """
        Proposals arrive for events that are not selling tickets online, so
        BOOKINGS_OFF resolves rather than erroring.

        Event.web_bookings defaults to False and save() derives
        accepting_web_bookings from it, so this fixture — like every other event
        in the suite — IS a bookings-off event. Asserted explicitly so the
        distinction is not accidental.
        """
        closed = make_event("CLOSED - PM")
        self.assertFalse(closed.accepting_web_bookings)
        self.user.assigned_events.add(closed)
        r = self.preview([self.row(**{"Event Code": "CLOSED - PM"})])
        entry = r.data["rows"][0]
        self.assertEqual(entry["classification"], "CREATE", entry)
        self.assertEqual(entry["event_code"], "CLOSED - PM")

    def test_code_is_stored_canonically_from_a_loose_input(self):
        gs = make_event("BIU/GS - PM")
        self.user.assigned_events.add(gs)
        r = self.preview([self.row(**{"Event Code": "biu/gs - pm"})])
        self.assertEqual(r.data["rows"][0]["event_code"], "BIU/GS - PM")

    def test_duplicate_of_stored_row_is_create_with_warning(self):
        ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Existing",
            email="Import.One@Example.com")       # different case on purpose
        r = self.preview([self.row()])
        entry = r.data["rows"][0]
        self.assertEqual(entry["classification"], "CREATE_WITH_WARNING")
        self.assertIn("already exists", entry["warning"])

    def test_a_file_duplicating_itself_warns_too(self):
        r = self.preview([self.row(), self.row()])
        self.assertEqual(r.data["rows"][0]["classification"], "CREATE")
        self.assertEqual(r.data["rows"][1]["classification"], "CREATE_WITH_WARNING")

    def test_counts_are_returned_per_category(self):
        rows = [self.row(**{"Email Address": "ok@x.com"}),
                self.row(**{"Event Code": "ZZZZ", "Email Address": "bad@x.com"}),
                self.row(**{"Email Address": "ok@x.com"})]
        r = self.preview(rows)
        self.assertEqual(r.data["counts"],
                         {"CREATE": 1, "CREATE_WITH_WARNING": 1, "ERROR": 1})
        self.assertEqual(r.data["importable"], 2)

    def test_mr_columns_with_content_reject_the_whole_file_for_non_mr(self):
        r = self.preview([self.row(**{"Internal Footnotes (MR)": "secret"})])
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("Internal Footnotes (MR)", str(r.data))

    def test_blank_mr_columns_are_fine_for_non_mr(self):
        r = self.preview([self.row(**{"Internal Footnotes (MR)": "",
                                      "Slot Recommendation by MR": ""})])
        self.assertEqual(r.status_code, 200, r.content)

    def test_mr_user_may_import_mr_content(self):
        r = self.preview([self.row(**{"Internal Footnotes (MR)": "notes"})],
                         user=self.mr_user)
        self.assertEqual(r.status_code, 200, r.content)


class ImportCommitTests(ImportBase):
    def test_commit_requires_a_plan_hash(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self.COMMIT, {"rows": [self.row()]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("plan_hash", r.data)

    def test_commit_writes_and_skips_error_rows(self):
        rows = [self.row(**{"Email Address": "good@x.com"}),
                self.row(**{"Event Code": "ZZZZ", "Email Address": "bad@x.com"})]
        preview = self.preview(rows)
        before = ProposalSubmission.objects.count()
        r = self.commit(rows, preview.data["plan_hash"])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["created"], 1)
        self.assertEqual(r.data["skipped"], 1)
        self.assertEqual(ProposalSubmission.objects.count(), before + 1)
        created = ProposalSubmission.objects.get(id=r.data["created_ids"][0])
        self.assertEqual(created.email, "good@x.com")
        self.assertEqual(created.created_by, self.user)
        self.assertEqual(created.submission_date, date(2026, 8, 10))

    def test_blank_date_stays_blank_no_create_path_default(self):
        rows = [self.row(**{"Submission Date": ""})]
        preview = self.preview(rows)
        r = self.commit(rows, preview.data["plan_hash"])
        self.assertEqual(r.status_code, 201, r.content)
        created = ProposalSubmission.objects.get(id=r.data["created_ids"][0])
        self.assertIsNone(created.submission_date,
                          "import must not apply the create-path default")

    def test_stale_hash_is_409_and_writes_nothing(self):
        rows = [self.row()]
        before = ProposalSubmission.objects.count()
        r = self.commit(rows, "deadbeef")
        self.assertEqual(r.status_code, 409, r.content)
        self.assertFalse(r.data["success"])
        self.assertTrue(r.data["plan_hash"])
        self.assertEqual(ProposalSubmission.objects.count(), before,
                         "never a partial write on a stale hash")

    def test_hash_changes_when_the_underlying_data_changes(self):
        rows = [self.row()]
        first = self.preview(rows).data["plan_hash"]
        # Same rows, but the event code now resolves somewhere else is not
        # reproducible; instead change the rows themselves.
        second = self.preview([self.row(**{"Speaker Name": "Different"})]).data["plan_hash"]
        self.assertNotEqual(first, second)

    def test_hash_is_stable_for_identical_input(self):
        rows = [self.row()]
        self.assertEqual(self.preview(rows).data["plan_hash"],
                         self.preview(rows).data["plan_hash"])

    def test_error_row_edits_do_not_invalidate_the_hash(self):
        """ERROR rows are never written, so they are excluded from the digest."""
        good = self.row(**{"Email Address": "g@x.com"})
        h1 = self.preview([good, self.row(**{"Event Code": "ZZZZ",
                                             "Email Address": "e1@x.com"})]).data["plan_hash"]
        h2 = self.preview([good, self.row(**{"Event Code": "QQQQ",
                                             "Email Address": "e2@x.com"})]).data["plan_hash"]
        self.assertEqual(h1, h2)

    def test_one_actionlog_per_batch_with_the_complete_id_list(self):
        rows = [self.row(**{"Email Address": f"c{i}@x.com"}) for i in range(6)]
        preview = self.preview(rows)
        before = ActionLog.objects.count()
        r = self.commit(rows, preview.data["plan_hash"], filename="zoho.xlsx")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(ActionLog.objects.count(), before + 1)
        log = ActionLog.objects.latest("id")
        self.assertIn("Imported 6 proposal submissions", log.action)
        self.assertIn("zoho.xlsx", log.details)
        for pk in r.data["created_ids"]:
            self.assertIn(str(pk), log.details)

    def test_commit_denied_without_the_module(self):
        self.client.force_authenticate(user=self.blind_user)
        r = self.client.post(self.COMMIT, {
            "rows": [self.row()], "plan_hash": "x",
            "import_batch_id": "11111111-1111-1111-1111-111111111111"},
            format="json")
        self.assertEqual(r.status_code, 403)


class ImportBatchIdentityTests(ImportBase):
    """
    C4 — a chunked commit failing partway through must still be identifiable:
    every row written by one logical file shares one import_batch_id, minted by
    preview and echoed through every chunk's commit. No undo endpoint is built —
    this is the answer to "what landed", not a way to reverse it.
    """

    def test_preview_mints_a_batch_id_when_none_is_supplied(self):
        r = self.preview([self.row()])
        self.assertTrue(r.data["import_batch_id"])
        # Parses as a real UUID, not just a truthy string.
        import uuid
        uuid.UUID(r.data["import_batch_id"])

    def test_preview_echoes_a_client_supplied_batch_id_unchanged(self):
        mine = "22222222-2222-2222-2222-222222222222"
        r = self.preview([self.row()], import_batch_id=mine)
        self.assertEqual(r.data["import_batch_id"], mine)

    def test_preview_rejects_a_malformed_batch_id(self):
        r = self.preview([self.row()], import_batch_id="not-a-uuid")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("import_batch_id", r.data)

    def test_commit_requires_the_batch_id(self):
        rows = [self.row()]
        preview = self.preview(rows)
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self.COMMIT, {
            "rows": rows, "plan_hash": preview.data["plan_hash"],
        }, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("import_batch_id", r.data)

    def test_every_created_row_is_stamped_with_the_supplied_batch_id(self):
        rows = [self.row(**{"Email Address": f"batch{i}@x.com"}) for i in range(3)]
        preview = self.preview(rows)
        batch_id = preview.data["import_batch_id"]
        r = self.commit(rows, preview.data["plan_hash"], import_batch_id=batch_id)
        self.assertEqual(r.status_code, 201, r.content)

        created = ProposalSubmission.objects.filter(id__in=r.data["created_ids"])
        self.assertEqual(created.count(), 3)
        for row in created:
            self.assertEqual(str(row.import_batch_id), batch_id)

    def test_two_chunks_of_one_file_share_the_batch_id(self):
        """
        Simulates a 2-chunk file: the SECOND chunk's preview is called with the
        FIRST chunk's minted id (as a real client would, holding it across
        chunks), and both chunks' commits land under that one id.
        """
        chunk1 = [self.row(**{"Email Address": "chunk1@x.com"})]
        chunk2 = [self.row(**{"Email Address": "chunk2@x.com"})]

        preview1 = self.preview(chunk1)
        batch_id = preview1.data["import_batch_id"]
        preview2 = self.preview(chunk2, import_batch_id=batch_id)
        self.assertEqual(preview2.data["import_batch_id"], batch_id)

        r1 = self.commit(chunk1, preview1.data["plan_hash"], import_batch_id=batch_id)
        r2 = self.commit(chunk2, preview2.data["plan_hash"], import_batch_id=batch_id)
        self.assertEqual(r1.status_code, 201, r1.content)
        self.assertEqual(r2.status_code, 201, r2.content)

        rows = ProposalSubmission.objects.filter(import_batch_id=batch_id)
        self.assertEqual(
            set(rows.values_list("email", flat=True)),
            {"chunk1@x.com", "chunk2@x.com"})

    def test_two_separate_files_get_two_different_batch_ids(self):
        b1 = self.preview([self.row(**{"Email Address": "f1@x.com"})]).data[
            "import_batch_id"]
        b2 = self.preview([self.row(**{"Email Address": "f2@x.com"})]).data[
            "import_batch_id"]
        self.assertNotEqual(b1, b2)

    def test_a_manually_created_proposal_has_no_batch_id(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post("/api/proposal-submissions/", {
            "event_code": "AFS - JS", "speaker_name": "Manual",
            "email": "manual.batch@x.com",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(
            ProposalSubmission.objects.get(id=r.data["id"]).import_batch_id)

    def test_duplicate_does_not_copy_the_batch_id(self):
        rows = [self.row(**{"Email Address": "dup.batch@x.com"})]
        preview = self.preview(rows)
        r = self.commit(rows, preview.data["plan_hash"],
                        import_batch_id=preview.data["import_batch_id"])
        source_id = r.data["created_ids"][0]

        dup = self.client.post(f"/api/proposal-submissions/{source_id}/duplicate/")
        self.assertEqual(dup.status_code, 201, dup.content)
        clone = ProposalSubmission.objects.get(id=dup.data["id"])
        self.assertIsNone(clone.import_batch_id)

    def test_import_batch_id_is_read_only_on_the_serializer(self):
        rows = [self.row(**{"Email Address": "readonly.batch@x.com"})]
        preview = self.preview(rows)
        r = self.commit(rows, preview.data["plan_hash"],
                        import_batch_id=preview.data["import_batch_id"])
        row_id = r.data["created_ids"][0]

        attacker_id = "33333333-3333-3333-3333-333333333333"
        patch = self.client.patch(f"/api/proposal-submissions/{row_id}/",
                                  {"import_batch_id": attacker_id}, format="json")
        self.assertEqual(patch.status_code, 200, patch.content)
        row = ProposalSubmission.objects.get(id=row_id)
        self.assertNotEqual(str(row.import_batch_id), attacker_id)

    def test_the_actionlog_names_the_batch_id(self):
        rows = [self.row(**{"Email Address": "logged.batch@x.com"})]
        preview = self.preview(rows)
        batch_id = preview.data["import_batch_id"]
        self.commit(rows, preview.data["plan_hash"], import_batch_id=batch_id)
        log = ActionLog.objects.latest("id")
        self.assertIn(batch_id, log.details)

    def test_filtering_by_import_batch_id_returns_only_that_batch(self):
        rows_a = [self.row(**{"Email Address": "filt.a@x.com"})]
        rows_b = [self.row(**{"Email Address": "filt.b@x.com"})]
        preview_a = self.preview(rows_a)
        preview_b = self.preview(rows_b)
        self.commit(rows_a, preview_a.data["plan_hash"],
                   import_batch_id=preview_a.data["import_batch_id"])
        self.commit(rows_b, preview_b.data["plan_hash"],
                   import_batch_id=preview_b.data["import_batch_id"])

        r = self.client.get("/api/proposal-submissions/", {
            "import_batch_id": preview_a.data["import_batch_id"]})
        self.assertEqual(r.status_code, 200, r.content)
        emails = {row["email"] for row in r.data["results"]}
        self.assertEqual(emails, {"filt.a@x.com"})


# ══ B. DUPLICATE DETECTION ═══════════════════════════════════════════════════

class DuplicateAnnotationTests(_Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.a = ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Eli Jasso",
            email="eli@example.com", submission_date=date(2026, 5, 1))
        cls.b = ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Eli Jasso",
            email="ELI@EXAMPLE.COM", submission_date=date(2026, 5, 2))
        cls.solo = ProposalSubmission.objects.create(
            event_code="BIUK - PM", speaker_name="Solo",
            email="solo@example.com", submission_date=date(2026, 5, 3))

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_pair_each_report_one_duplicate_case_insensitively(self):
        r = self.client.get(self.LIST, {"page_size": 50})
        counts = {row["id"]: row["duplicate_count"] for row in r.data["results"]}
        self.assertEqual(counts[self.a.id], 1)
        self.assertEqual(counts[self.b.id], 1)
        self.assertEqual(counts[self.solo.id], 0)

    def test_same_email_different_event_is_not_a_duplicate(self):
        ProposalSubmission.objects.create(
            event_code="BIUK - PM", speaker_name="Eli Jasso",
            email="eli@example.com")
        r = self.client.get(self.LIST, {"page_size": 50})
        counts = {row["id"]: row["duplicate_count"] for row in r.data["results"]}
        self.assertEqual(counts[self.a.id], 1, "still only the AFS pair")

    def test_has_duplicates_filter(self):
        r = self.client.get(self.LIST, {"has_duplicates": "true", "page_size": 50})
        self.assertEqual(sorted(row["id"] for row in r.data["results"]),
                         sorted([self.a.id, self.b.id]))
        r = self.client.get(self.LIST, {"has_duplicates": "false", "page_size": 50})
        self.assertIn(self.solo.id, [row["id"] for row in r.data["results"]])
        self.assertNotIn(self.a.id, [row["id"] for row in r.data["results"]])

    def test_count_is_scoped_so_out_of_scope_peers_read_as_none(self):
        """Documented consequence: a duplicate outside scope shows as 0."""
        scoped_user = U.objects.create_user(
            username="dup_scoped", password="x", email="ds@x.com",
            role="sales", team=self.role)
        scoped_user.assigned_events.set([self.other_event])   # BIUK only
        ProposalSubmission.objects.create(
            event_code="BIUK - PM", speaker_name="Solo", email="solo@example.com")
        self.client.force_authenticate(user=scoped_user)
        r = self.client.get(self.LIST, {"page_size": 50})
        codes = {row["event_code"] for row in r.data["results"]}
        self.assertEqual(codes, {"BIUK - PM"})
        # The AFS pair is invisible, so nothing here reports them.
        self.assertNotIn(self.a.id, [row["id"] for row in r.data["results"]])

    def test_create_returns_201_with_a_non_blocking_warning(self):
        r = self.client.post(self.LIST, {
            "event_code": "AFS - JS", "speaker_name": "Eli Jasso",
            "email": "eli@example.com",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIn("warning", r.data)
        self.assertEqual(r.data["duplicate_count"], 2)

    def test_duplicate_action_returns_201_with_a_warning(self):
        r = self.client.post(f"{self.LIST}{self.solo.id}/duplicate/")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIn("warning", r.data)
        self.assertEqual(r.data["duplicate_count"], 1)

    def test_no_warning_when_there_is_no_duplicate(self):
        r = self.client.post(self.LIST, {
            "event_code": "AFS - JS", "speaker_name": "Fresh Face",
            "email": "fresh@example.com",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertNotIn("warning", r.data)

    def test_duplicate_count_is_read_only(self):
        r = self.client.patch(f"{self.LIST}{self.solo.id}/",
                              {"duplicate_count": 99}, format="json")
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get(f"{self.LIST}{self.solo.id}/")
        self.assertEqual(r2.data["duplicate_count"], 0)


# ══ C. MR WRITE RULE, REMAINING PATHS ════════════════════════════════════════

class MRWritePathTests(_Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mr = U.objects.create_user(
            username="c_mr", password="x", email="cmr@x.com",
            role="market_research", team=cls.role)
        cls.mr.assigned_events.set([cls.event])
        cls.row = ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Has Notes", email="hn@x.com",
            internal_footnotes_mr="original notes",
            slot_recommendation_mr="original rec")

    def test_c1_mr_user_can_clear_an_mr_field_to_blank(self):
        """
        The drop-blank-echo rule must NOT apply to MR users, or they could never
        delete their own notes.
        """
        self.client.force_authenticate(user=self.mr)
        r = self.client.patch(f"{self.LIST}{self.row.id}/",
                              {"internal_footnotes_mr": ""}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.internal_footnotes_mr, "")

    def test_c1_admin_can_clear_an_mr_field_too(self):
        admin = U.objects.create_user(
            username="c_admin", password="x", email="ca@x.com",
            role="admin", team=self.role)
        self.client.force_authenticate(user=admin)
        r = self.client.patch(f"{self.LIST}{self.row.id}/",
                              {"slot_recommendation_mr": ""}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.slot_recommendation_mr, "")

    def test_c1_non_mr_blank_echo_still_preserves_the_value(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.patch(f"{self.LIST}{self.row.id}/",
                              {"company_name": "Co", "internal_footnotes_mr": ""},
                              format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.internal_footnotes_mr, "original notes")

    def test_c2_create_with_mr_content_is_400_for_non_permitted(self):
        """No stored value to compare against — must refuse, not drop."""
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self.LIST, {
            "event_code": "AFS - JS", "speaker_name": "New", "email": "n@x.com",
            "internal_footnotes_mr": "sneaky content",
        }, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("internal_footnotes_mr", r.data)

    def test_c2_create_with_blank_mr_field_is_fine(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self.LIST, {
            "event_code": "AFS - JS", "speaker_name": "New2", "email": "n2@x.com",
            "internal_footnotes_mr": "", "slot_recommendation_mr": "",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_c2_mr_user_can_create_with_mr_content(self):
        self.client.force_authenticate(user=self.mr)
        r = self.client.post(self.LIST, {
            "event_code": "AFS - JS", "speaker_name": "New3", "email": "n3@x.com",
            "internal_footnotes_mr": "legitimate notes",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(
            ProposalSubmission.objects.get(id=r.data["id"]).internal_footnotes_mr,
            "legitimate notes")


# ══ E. CSV EXPORT ════════════════════════════════════════════════════════════

class ExportTests(_Base):
    EXPORT = "/api/proposal-submissions/export/"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mr = U.objects.create_user(
            username="e_mr", password="x", email="emr@x.com",
            role="market_research", team=cls.role)
        cls.mr.assigned_events.set([cls.event, cls.other_event])
        ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Alpha", email="al@x.com",
            submission_date=date(2026, 1, 1), qc_grade="A",
            internal_footnotes_mr="hidden notes",
            slot_recommendation_mr="hidden rec")
        ProposalSubmission.objects.create(
            event_code="BIUK - PM", speaker_name="Beta", email="be@x.com",
            submission_date=date(2026, 2, 1), qc_grade="B")

    def body(self, response):
        return b"".join(response.streaming_content).decode("utf-8")

    def test_export_streams_csv_with_a_filename(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.get(self.EXPORT)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/csv")
        self.assertIn("proposal-submissions.csv", r["Content-Disposition"])
        self.assertTrue(hasattr(r, "streaming_content"))

    def test_headers_are_zoho_labels_so_it_round_trips(self):
        self.client.force_authenticate(user=self.mr)
        header = self.body(self.client.get(self.EXPORT)).splitlines()[0]
        for label in ("Event Code", "Email Address", "Speaker Name",
                      "Slot Recommendation by MR", "Internal Footnotes (MR)"):
            self.assertIn(label, header)
        mapping, unknown = map_headers([h.strip() for h in header.split(",")])
        self.assertEqual(unknown, [], "export headers must re-import cleanly")

    def test_export_strips_mr_columns_for_non_mr(self):
        self.client.force_authenticate(user=self.user)
        text = self.body(self.client.get(self.EXPORT))
        self.assertNotIn("Internal Footnotes (MR)", text)
        self.assertNotIn("hidden notes", text)

    def test_export_includes_mr_columns_for_mr(self):
        self.client.force_authenticate(user=self.mr)
        text = self.body(self.client.get(self.EXPORT))
        self.assertIn("Internal Footnotes (MR)", text)
        self.assertIn("hidden notes", text)

    def test_export_respects_active_filters(self):
        self.client.force_authenticate(user=self.user)
        text = self.body(self.client.get(self.EXPORT, {"qc_grade": "A"}))
        self.assertIn("Alpha", text)
        self.assertNotIn("Beta", text)

    def test_export_respects_search(self):
        self.client.force_authenticate(user=self.user)
        text = self.body(self.client.get(self.EXPORT, {"search": "Beta"}))
        self.assertIn("Beta", text)
        self.assertNotIn("Alpha", text)

    def test_export_respects_ordering(self):
        self.client.force_authenticate(user=self.user)
        asc = self.body(self.client.get(self.EXPORT, {"ordering": "speaker_name"}))
        desc = self.body(self.client.get(self.EXPORT, {"ordering": "-speaker_name"}))
        self.assertLess(asc.index("Alpha"), asc.index("Beta"))
        self.assertLess(desc.index("Beta"), desc.index("Alpha"))

    def test_export_respects_rbac_scope(self):
        scoped = U.objects.create_user(
            username="e_scoped", password="x", email="es@x.com",
            role="sales", team=self.role)
        scoped.assigned_events.set([self.other_event])       # BIUK only
        self.client.force_authenticate(user=scoped)
        text = self.body(self.client.get(self.EXPORT))
        self.assertIn("Beta", text)
        self.assertNotIn("Alpha", text)

    def test_export_is_empty_for_an_unassigned_user(self):
        nobody = U.objects.create_user(
            username="e_none", password="x", email="en@x.com",
            role="sales", team=self.role)
        self.client.force_authenticate(user=nobody)
        text = self.body(self.client.get(self.EXPORT))
        self.assertEqual(len(text.strip().splitlines()), 1, "header only")

    def test_export_denied_without_the_module(self):
        self.client.force_authenticate(user=self.blind_user)
        self.assertEqual(self.client.get(self.EXPORT).status_code, 403)


# ══ F. DISTINCT FILTER OPTIONS ═══════════════════════════════════════════════

class FilterOptionsTests(_Base):
    OPTIONS = "/api/proposal-submissions/filter_options/"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="A", email="a@x.com",
            participation_type="Speaker", qc_grade="B",
            speaker_slot_status="Confirmed", revenue_possibility="High")
        ProposalSubmission.objects.create(
            event_code="BIUK - PM", speaker_name="B", email="b@x.com",
            participation_type="Panelist", qc_grade="",
            sponsorship_status="Pending")

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_returns_only_values_actually_stored(self):
        r = self.client.get(self.OPTIONS)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["participation_type"], ["Panelist", "Speaker"])
        self.assertEqual(r.data["qc_grade"], ["B"], "blank excluded")
        self.assertNotIn("Sponsor", r.data["participation_type"],
                         "placeholder value nobody uses must not appear")

    def test_covers_all_five_dropdown_fields(self):
        r = self.client.get(self.OPTIONS)
        for field in ("participation_type", "qc_grade", "speaker_slot_status",
                      "sponsorship_status", "revenue_possibility"):
            self.assertIn(field, r.data)

    def test_options_are_rbac_scoped(self):
        scoped = U.objects.create_user(
            username="f_scoped", password="x", email="fs@x.com",
            role="sales", team=self.role)
        scoped.assigned_events.set([self.other_event])       # BIUK only
        self.client.force_authenticate(user=scoped)
        r = self.client.get(self.OPTIONS)
        self.assertEqual(r.data["participation_type"], ["Panelist"])

    def test_filter_schema_choices_come_from_stored_values(self):
        r = self.client.get(f"{self.LIST}filter_schema/")
        self.assertEqual(
            sorted(r.data["fields"]["participation_type"]["choices"]),
            ["Panelist", "Speaker"])


# ══ G. SCOPE-REVIEW ITEMS ════════════════════════════════════════════════════

class PermittedEventsTests(_Base):
    URL = "/api/proposal-submissions/permitted_events/"

    def test_scoped_user_sees_only_assigned_events(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.data["unrestricted"])
        self.assertEqual(sorted(e["event_code"] for e in r.data["results"]),
                         ["AFS - JS", "BIUK - PM"])

    def test_unassigned_user_sees_none(self):
        nobody = U.objects.create_user(
            username="g_none", password="x", email="gn@x.com",
            role="sales", team=self.role)
        self.client.force_authenticate(user=nobody)
        r = self.client.get(self.URL)
        self.assertEqual(r.data["count"], 0)

    def test_admin_sees_the_whole_catalogue(self):
        make_event("EXTRA - ZZ")
        admin = U.objects.create_user(
            username="g_admin", password="x", email="ga@x.com",
            role="admin", team=self.role)
        self.client.force_authenticate(user=admin)
        r = self.client.get(self.URL)
        self.assertTrue(r.data["unrestricted"])
        self.assertIn("EXTRA - ZZ", [e["event_code"] for e in r.data["results"]])

    def test_picker_and_validator_agree(self):
        """
        A2 — every code the picker offers must create successfully.

        This endpoint existed from the start but NOTHING READ IT: ProposalFormModal
        fetched the full events catalogue instead, so a scoped user was offered all
        142 codes and every one they were not assigned to answered 400 on save.
        The modal now reads this endpoint (api/proposalSubmission.js:
        permittedEvents), which is what makes this test a statement about the real
        picker rather than about an unused endpoint.
        """
        self.client.force_authenticate(user=self.user)
        offered = [e["event_code"] for e in self.client.get(self.URL).data["results"]]
        self.assertTrue(offered)
        for i, code in enumerate(offered):
            with self.subTest(code=code):
                r = self.client.post(self.LIST, {
                    "event_code": code, "speaker_name": f"P{i}",
                    "email": f"p{i}@x.com",
                }, format="json")
                self.assertEqual(r.status_code, 201, r.content)

    def test_the_picker_offers_strictly_fewer_codes_than_the_catalogue(self):
        """
        The bug this fixes, stated as a number: the catalogue holds codes this user
        cannot use, so a picker reading the catalogue is offering guaranteed-400
        options.
        """
        from events.models import Event
        make_event("UNASSIGNED - AA")
        make_event("UNASSIGNED - BB")
        self.client.force_authenticate(user=self.user)
        offered = self.client.get(self.URL).data["count"]
        self.assertLess(offered, Event.objects.count())


class MRQueryLeakTests(_Base):
    """G2 — a user who cannot read the MR columns cannot filter or sort by them."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mr = U.objects.create_user(
            username="g2_mr", password="x", email="g2@x.com",
            role="market_research", team=cls.role)
        cls.mr.assigned_events.set([cls.event])
        ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Secret", email="s@x.com",
            internal_footnotes_mr="confidential")

    def test_filter_schema_hides_the_mr_fields(self):
        self.client.force_authenticate(user=self.user)
        fields = self.client.get(f"{self.LIST}filter_schema/").data["fields"]
        self.assertNotIn("internal_footnotes_mr", fields)
        self.assertNotIn("slot_recommendation_mr", fields)

    def test_filter_schema_shows_them_to_mr(self):
        self.client.force_authenticate(user=self.mr)
        fields = self.client.get(f"{self.LIST}filter_schema/").data["fields"]
        self.assertIn("internal_footnotes_mr", fields)
        self.assertIn("slot_recommendation_mr", fields)

    def test_filtering_on_an_mr_field_is_400(self):
        self.client.force_authenticate(user=self.user)
        for params in ({"internal_footnotes_mr": "confidential"},
                       {"internal_footnotes_mr__icontains": "conf"},
                       {"slot_recommendation_mr": "x"}):
            with self.subTest(params=params):
                r = self.client.get(self.LIST, params)
                self.assertEqual(r.status_code, 400, r.content)

    def test_ordering_by_an_mr_field_is_400(self):
        self.client.force_authenticate(user=self.user)
        for value in ("internal_footnotes_mr", "-slot_recommendation_mr",
                      "speaker_name,internal_footnotes_mr"):
            with self.subTest(ordering=value):
                r = self.client.get(self.LIST, {"ordering": value})
                self.assertEqual(r.status_code, 400, r.content)

    def test_filter_spec_on_an_mr_field_is_400(self):
        import json
        from urllib.parse import quote
        spec = {"match": "all", "criteria": [
            {"field": "internal_footnotes_mr", "op": "contains",
             "value": "conf"}]}
        self.client.force_authenticate(user=self.user)
        r = self.client.get(f"{self.LIST}?filter_spec={quote(json.dumps(spec))}")
        self.assertEqual(r.status_code, 400, r.content)

    def test_mr_user_may_filter_and_order_by_them(self):
        self.client.force_authenticate(user=self.mr)
        self.assertEqual(
            self.client.get(self.LIST,
                            {"ordering": "internal_footnotes_mr"}).status_code, 200)

    def test_ordinary_filters_and_ordering_still_work(self):
        self.client.force_authenticate(user=self.user)
        self.assertEqual(self.client.get(self.LIST, {"qc_grade": "B"}).status_code, 200)
        self.assertEqual(
            self.client.get(self.LIST, {"ordering": "-speaker_name"}).status_code, 200)

    def test_bulk_update_schema_never_exposed_the_mr_fields(self):
        """Verified, not assumed."""
        self.client.force_authenticate(user=self.user)
        fields = self.client.get(f"{self.LIST}bulk_update_schema/").data["fields"]
        self.assertNotIn("internal_footnotes_mr", fields)
        self.assertNotIn("slot_recommendation_mr", fields)


class BusinessTimezoneTests(_Base):
    """G3 — the create-path default resolves in IST while storage stays UTC."""

    def test_frozen_utc_instant_inside_the_window_yields_the_ist_date(self):
        # 2026-08-10 20:00 UTC == 2026-08-11 01:30 IST. UTC says the 10th; the
        # team is already on the 11th, and 11 is the answer.
        frozen = datetime(2026, 8, 10, 20, 0, tzinfo=dt_timezone.utc)
        with patch("proposal_submission.serializers.timezone.now",
                   return_value=frozen):
            self.assertEqual(business_today(), date(2026, 8, 11))

            self.client.force_authenticate(user=self.user)
            body = {"event_code": "AFS - JS", "speaker_name": "Early Bird",
                    "email": "early@x.com"}
            r = self.client.post(self.LIST, body, format="json")
            self.assertEqual(r.status_code, 201, r.content)
            self.assertEqual(str(r.data["submission_date"]), "2026-08-11")

    def test_midday_utc_is_the_same_day_in_both_zones(self):
        frozen = datetime(2026, 8, 10, 9, 0, tzinfo=dt_timezone.utc)
        with patch("proposal_submission.serializers.timezone.now",
                   return_value=frozen):
            self.assertEqual(business_today(), date(2026, 8, 10))

    def test_settings_were_not_changed(self):
        from django.conf import settings
        self.assertEqual(settings.TIME_ZONE, "UTC")
        self.assertTrue(settings.USE_TZ)
