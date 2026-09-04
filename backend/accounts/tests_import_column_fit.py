"""
accounts/tests_import_column_fit.py
────────────────────────────────────
A value that does not fit its database column must be a ROW ERROR in the preview,
never a 500 at commit.

THE INCIDENT THIS COMES FROM
Both importers classified rows on business rules alone, meaning required fields,
event code, dates and numeric ranges. Neither checked whether a value would
physically fit its column. Commit writes with a plain save(), which runs no field
validation, so an overlong value passed preview as CREATE and then raised a psycopg
DataError inside the commit's transaction.atomic(). That is a 500 rather than a
readable row error, and because the whole chunk shares one atomic block, the other
499 rows in it were rolled back too.

It hit both modules at once, on the real Zoho exports:

    All Paper Reviews.csv        355 rows graded 'B+' against grade CharField(1)
    All Proposal Submissions.csv   2 rows with an outreach message pasted into
    All Paper Reviews.csv          Speaker Name, 255 chars against 150

Every commit of either file returned 500 and imported nothing at all.

Two fixes are asserted here. accounts/import_common.column_errors makes any
non-fitting value a row error at PREVIEW time, so the commit cannot be surprised;
and grade was widened to 5, because a one-character grade column was simply wrong
about the data.

Asserted for BOTH modules, since they share the helper and the failure mode.

    python manage.py test accounts.tests_import_column_fit
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from rest_framework.test import APIClient

from events.models import Event
from paper_review.models import PaperReview
from proposal_submission.models import ProposalSubmission

User = get_user_model()

CODE = "COLFIT - AA"

# The pasted-outreach value that actually broke both files, at its real length.
PASTED_MESSAGE = (
    "Hello Frank,  I see you have a strong background in people management and "
    "data analysis, and I wanted to reach out about speaking at our upcoming "
    "summit. The agenda is coming together now and I think your perspective on "
    "terminal operations would land really well with this audience. "
)[:255].ljust(255, ".")


def pr_row(**over):
    row = {
        "Event Code": CODE,
        "Speaker Name": "Fit Speaker",
        "Company Name": "Fit Ltd",
        "Email Address of the Speaker": "fit@example.com",
        "Grade": "B",
    }
    row.update(over)
    return row


def ps_row(**over):
    # Proposals name the email column "Email Address"; paper reviews spell it
    # "Email Address of the Speaker". Each mirrors its own Zoho form.
    row = {
        "Event Code": CODE,
        "Speaker Name": "Fit Speaker",
        "Company Name": "Fit Ltd",
        "Email Address": "fit@example.com",
        "QC Grade": "B",
    }
    row.update(over)
    return row


class _Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.hp = User.objects.create_user(
            username="HP", password="x", email="hp@iq-hub.com", role="admin",
        )
        Event.objects.create(event_code=CODE, official_event_name="Fit Event",
                             event_date="2026-09-01")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.hp)

    def preview(self, base, rows):
        resp = self.client.post(base + "preview/", {"rows": rows}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.data

    def commit(self, base, rows, preview_data):
        return self.client.post(base + "commit/", {
            "rows": rows,
            "plan_hash": preview_data["plan_hash"],
            "import_batch_id": preview_data["import_batch_id"],
            "filename": "fit.csv",
        }, format="json")

    def problems(self, row_entry):
        return " ".join(f"{e['field']}: {e['problem']}" for e in row_entry["errors"])


PR = ("paper_review", "/api/paper-reviews/import/", pr_row, PaperReview)
PS = ("proposal_submission", "/api/proposal-submissions/import/", ps_row,
      ProposalSubmission)
MODULES = [PR, PS]


class OverlongValueIsARowErrorTests(_Base):

    def test_an_overlong_speaker_name_errors_in_the_preview(self):
        """
        The exact value from both real exports. It must be refused where the user
        can read why, with the column named, the limit stated and the actual length
        given, rather than at the database.
        """
        for module, base, row, _ in MODULES:
            with self.subTest(module=module):
                data = self.preview(base, [row(**{"Speaker Name": PASTED_MESSAGE})])
                entry = data["rows"][0]
                self.assertEqual(entry["classification"], "ERROR")
                self.assertIn("Speaker Name", self.problems(entry))
                self.assertIn("longer than 150 characters (255)",
                              self.problems(entry))
                self.assertEqual(data["importable"], 0)

    def test_committing_that_file_is_a_201_that_skips_the_row_not_a_500(self):
        """
        THE regression. This request returned
        'POST /api/proposal-submissions/import/commit/ HTTP/1.1 500' before the
        fix, on both modules.
        """
        for module, base, row, model in MODULES:
            with self.subTest(module=module):
                rows = [row(**{"Speaker Name": PASTED_MESSAGE})]
                data = self.preview(base, rows)
                resp = self.commit(base, rows, data)
                self.assertEqual(resp.status_code, 201, resp.content)
                self.assertEqual(resp.data["created"], 0)
                self.assertEqual(resp.data["skipped"], 1)
                self.assertEqual(model.objects.count(), 0)

    def test_one_bad_row_no_longer_discards_the_good_rows_around_it(self):
        """
        The costly half of the old failure. The commit writes a chunk inside one
        transaction.atomic(), so a DataError on row 246 rolled back the 499 valid
        rows sharing that chunk; the user saw a 500 and an empty table.
        """
        for module, base, row, model in MODULES:
            with self.subTest(module=module):
                model.objects.all().delete()
                rows = [
                    row(**{"Speaker Name": "Good One"}),
                    row(**{"Speaker Name": PASTED_MESSAGE}),
                    row(**{"Speaker Name": "Good Two"}),
                ]
                data = self.preview(base, rows)
                self.assertEqual(data["importable"], 2)

                resp = self.commit(base, rows, data)
                self.assertEqual(resp.status_code, 201, resp.content)
                self.assertEqual(resp.data["created"], 2)
                self.assertEqual(resp.data["skipped"], 1)
                self.assertEqual(
                    sorted(model.objects.values_list("speaker_name", flat=True)),
                    ["Good One", "Good Two"],
                )

    def test_a_value_that_fits_exactly_is_not_refused(self):
        """The boundary is inclusive; 150 characters is legal, 151 is not."""
        for module, base, row, _ in MODULES:
            with self.subTest(module=module):
                exact = "N" * 150
                self.assertEqual(
                    self.preview(base, [row(**{"Speaker Name": exact})])["importable"],
                    1)
                over = "N" * 151
                self.assertEqual(
                    self.preview(base, [row(**{"Speaker Name": over})])["importable"],
                    0)

    def test_an_integer_too_large_for_its_column_is_a_row_error_too(self):
        """
        Same class of failure, non-text: a follower count above 2147483647 does not
        fit PositiveIntegerField and raised the identical DataError 500.
        """
        cases = [
            (PR, {"LinkedIn Followers Count of Speaker": 9999999999}),
            (PS, {"LinkedIn Followers": 9999999999}),
        ]
        for (module, base, row, _), over in cases:
            with self.subTest(module=module):
                data = self.preview(base, [row(**over)])
                entry = data["rows"][0]
                self.assertEqual(entry["classification"], "ERROR",
                                 self.problems(entry))
                self.assertIn("outside the range this column stores",
                              self.problems(entry))

    def test_an_overlong_url_still_reports_the_way_it_always_did(self):
        """
        The generic check replaced a hand-written URL_FIELDS check. The two
        LinkedIn columns are URLField(max_length=500), so the message must be
        unchanged for them; this is the one case that already worked.
        """
        cases = [
            (PR, {"LinkedIn Profile of Speaker": "https://x.example/" + "u" * 600}),
            (PS, {"LinkedIn (Speaker)": "https://x.example/" + "u" * 600}),
        ]
        for (module, base, row, _), over in cases:
            with self.subTest(module=module):
                entry = self.preview(base, [row(**over)])["rows"][0]
                self.assertEqual(entry["classification"], "ERROR")
                self.assertIn("longer than 500 characters", self.problems(entry))


# A rubric that sums to 33, which is inside the B+ band (31-35). Spelled with
# the Zoho headers because that is what an import file carries.
B_PLUS_SCORES = {
    "Closeness to Topic (10)": 10,
    "Closeness to Region (5)": 5,
    "Clear Solution to Challenges (10)": 10,
    "Case Study, Results, Examples (5)": 5,
    "Not an obvious 'Sales Pitch' (5)": 3,
    "Company Profile (10)": 0,
}


class GradeWidthTests(_Base):
    """
    grade was CharField(max_length=1) on an assumption, not on the data. 'B+' is
    the third most common grade in the real export, 355 of 3492 rows, and every one
    of them was unimportable.

    WHAT CHANGED UNDER THIS CLASS, AND WHY THE ASSERTIONS MOVED
    grade is DERIVED now. PaperReview.save() overwrites it from the six criteria
    on every write (models.py computed_grade(), GRADE_BANDS), so the file's Grade
    column is accepted for round-trip compatibility and then decides nothing —
    paper_review/importer.py says so in as many words. These tests asserted the
    opposite, that an imported letter is stored verbatim, and they had been
    failing ever since the bands became business rules.

    The column still has to be five wide, and that is what is worth testing: not
    because a file can carry "B+", but because computed_grade() RETURNS it. So
    the width is now asserted through the derivation that produces it.
    """

    def test_a_file_carrying_b_plus_is_still_importable(self):
        """
        Round-trip compatibility. The column is accepted rather than refused;
        refusing it would break every existing export that carries one.
        """
        rows = [pr_row(**{"Grade": "B+"})]
        data = self.preview(PR[1], rows)
        self.assertEqual(data["importable"], 1, data["rows"])
        self.assertEqual(self.commit(PR[1], rows, data).status_code, 201)

    def test_the_derived_b_plus_is_stored_whole(self):
        """
        THE REASON THE COLUMN IS FIVE WIDE. A 33-point rubric is a B+, and a
        one-character column truncated it — which is the same DataError this file
        exists for, just reached through save() rather than through the file.
        """
        rows = [pr_row(**{"Grade": "B+"}, **B_PLUS_SCORES)]
        data = self.preview(PR[1], rows)

        resp = self.commit(PR[1], rows, data)
        self.assertEqual(resp.status_code, 201, resp.content)
        review = PaperReview.objects.get()
        self.assertEqual(review.proposal_score, 33)
        self.assertEqual(review.grade, "B+")

    def test_the_criteria_outrank_the_files_grade(self):
        """
        A file claiming A over a 33-point rubric stores B+. Every letter the
        export carries goes in; none of them decides anything.
        """
        for claimed in ("A", "B", "B+", "C", "D", "E"):
            with self.subTest(grade=claimed):
                PaperReview.objects.all().delete()
                rows = [pr_row(**{"Grade": claimed}, **B_PLUS_SCORES)]
                data = self.preview(PR[1], rows)
                resp = self.commit(PR[1], rows, data)
                self.assertEqual(resp.status_code, 201, resp.content)
                self.assertEqual(PaperReview.objects.get().grade, "B+")

    def test_the_column_is_still_narrow_enough_to_mean_something(self):
        """
        Widened for a modifier, not opened up to free text. The bridge in
        proposal_bridge.py copies this into qc_grade, so it must stay the narrower
        of the two; tests_paper_to_proposal.py asserts that relationship.
        """
        entry = self.preview(PR[1], [pr_row(**{"Grade": "EXCELLENT"})])["rows"][0]
        self.assertEqual(entry["classification"], "ERROR")
        self.assertIn("longer than 5 characters", self.problems(entry))
