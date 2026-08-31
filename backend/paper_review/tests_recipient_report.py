"""
paper_review/tests_recipient_report.py
─────────────────────────────────────────
B2 / D1 — the read-only recipient-resolution report command.

Guards the one property that matters most for a report meant to de-risk flipping
PAPER_REVIEW_NOTIFICATIONS_ENABLED: that it truly sends and writes nothing, no
matter what it finds.

D1 additionally pins the CATALOGUE-WIDE behaviour. The command's whole purpose is
to answer "does the agreed To: recipient exist in the data?", and answering that
from the codes present in the pipeline tables would answer it from a sample of the
events that happen to have a review already — which is how a previous pass reached
a conclusion from ONE event. The tests below pin, in order:

  * scope="all" (the default) reports catalogue events with NO pipeline row
  * scope="pipeline" genuinely narrows, so the default is not accidentally equal
  * DEGRADED is distinguished from FALLBACK — the case where sales_executive is
    null but a speaker_sales/market_research assignee exists, which is 11 of the
    142 real events and was previously untested
  * the readiness counts, and all three branches of the verdict
"""
from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from events.models import Event
from paper_review.models import NotificationLog, PaperReview
from proposal_submission.models import ProposalSubmission

U = get_user_model()

# Stands in for settings.PAPER_REVIEW_CC_EMAILS. Pinned on every class below so
# the report's output never quotes a live address, and so the counts under test
# do not move when the real list is edited.
REPORT_CC = ["fixed.one@example.invalid", "fixed.two@example.invalid"]
REPORT_CC_JOINED = ", ".join(REPORT_CC)


def run(*args):
    out = StringIO()
    call_command("report_paper_review_recipients", *args, stdout=out)
    return out.getvalue()


def make_user(username, role, email=None):
    return U.objects.create_user(
        username=username, password="x",
        email=email if email is not None else f"{username}@example.com",
        role=role)


def make_event(code, sales_executive=None, cc_users=()):
    event = Event.objects.create(
        event_code=code, official_event_name=f"Event {code}",
        event_date=date(2026, 5, 1), sales_executive=sales_executive,
    )
    if cc_users:
        event.assigned_users.set(cc_users)
    return event


@override_settings(PAPER_REVIEW_CC_EMAILS=REPORT_CC)
class RecipientReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        U = get_user_model()

        cls.exec_user = U.objects.create_user(
            username="rr_exec", password="x", email="rr.exec@example.com",
            role="sales")
        cls.resolved_event = Event.objects.create(
            event_code="RR - OK", official_event_name="Resolved Event",
            event_date=date(2026, 5, 1), sales_executive=cls.exec_user,
        )
        cls.orphan_event = Event.objects.create(
            event_code="RR - ORPHAN", official_event_name="No Sales Exec",
            event_date=date(2026, 5, 1),
        )
        PaperReview.objects.create(
            event_code="RR - OK", speaker_name="A", email="a@example.com",
            paper_submission_date=date(2026, 5, 1))
        PaperReview.objects.create(
            event_code="RR - ORPHAN", speaker_name="B", email="b@example.com",
            paper_submission_date=date(2026, 5, 1))
        # Proposal-only code and a stored code with no matching Event at all —
        # both tables must be swept, and a genuinely unresolvable code must show.
        ProposalSubmission.objects.create(
            event_code="RR - PROPOSAL-ONLY", speaker_name="C", email="c@example.com")
        ProposalSubmission.objects.create(
            event_code="RR - GHOST", speaker_name="D", email="d@example.com")

    def test_sends_no_mail(self):
        run()
        self.assertEqual(len(mail.outbox), 0)

    def test_writes_no_notification_log_rows(self):
        run()
        self.assertEqual(NotificationLog.objects.count(), 0)

    def test_creates_no_paper_review_or_proposal_rows(self):
        before_reviews = PaperReview.objects.count()
        before_proposals = ProposalSubmission.objects.count()
        run()
        self.assertEqual(PaperReview.objects.count(), before_reviews)
        self.assertEqual(ProposalSubmission.objects.count(), before_proposals)

    def test_sweeps_codes_from_both_tables(self):
        out = run()
        for code in ("RR - OK", "RR - ORPHAN", "RR - PROPOSAL-ONLY", "RR - GHOST"):
            with self.subTest(code=code):
                self.assertIn(code, out)

    def test_a_resolved_event_reports_resolved_with_its_sales_exec(self):
        out = run()
        self.assertIn("RESOLVED", out)
        self.assertIn("rr.exec@example.com", out)

    def test_an_event_with_no_sales_exec_degrades_to_the_fixed_cc(self):
        """
        Asserted on the ROW, not on the whole output: the tail warning about
        EVENT_NOT_FOUND rows contains the word FALLBACK too, so a bare assertIn
        would pass whatever this event actually resolved to.
        """
        row = next(l for l in run().splitlines() if "RR - ORPHAN" in l)
        self.assertIn("DEGRADED", row)
        self.assertIn(REPORT_CC_JOINED, row)
        self.assertIn("no_sales_executive", row)

    @override_settings(PAPER_REVIEW_CC_EMAILS=[])
    def test_with_no_fixed_cc_the_same_event_reports_fallback(self):
        row = next(l for l in run().splitlines() if "RR - ORPHAN" in l)
        self.assertIn("FALLBACK", row)

    def test_a_code_with_no_matching_event_reports_event_not_found(self):
        out = run()
        self.assertIn("EVENT_NOT_FOUND", out)

    def test_csv_format_is_parseable_and_has_the_expected_columns(self):
        import csv
        from io import StringIO as SIO
        out = run("--format", "csv")
        rows = list(csv.DictReader(SIO(out)))
        self.assertEqual(set(rows[0].keys()),
                         {"event_code", "outcome", "to", "cc", "note"})
        codes = {r["event_code"] for r in rows}
        self.assertIn("RR - OK", codes)

    def test_summary_counts_match_the_row_outcomes(self):
        out = run()
        self.assertIn("resolved", out.lower())
        self.assertIn("event_not_found", out.lower())


# ══ D1. CATALOGUE-WIDE READINESS ═════════════════════════════════════════════

def readiness_count(out, label):
    """
    The integer on the readiness line containing `label`. Parsed rather than
    string-matched on exact spacing, so a column-width change does not fail a test
    about a NUMBER.
    """
    for line in out.splitlines():
        if label in line:
            return int(line.strip().split()[-1])
    raise AssertionError(f"no readiness line containing {label!r} in:\n{out}")


@override_settings(PAPER_REVIEW_CC_EMAILS=REPORT_CC)
class CatalogueWideScopeTests(TestCase):
    """
    D1 — the report must cover EVERY event in the catalogue, not only codes that
    already have a paper review or proposal against them.
    """

    @classmethod
    def setUpTestData(cls):
        cls.exec_user = make_user("d1_exec", "sales")
        cls.cc_user = make_user("d1_cc", "speaker_sales")

        # In the catalogue, but in NEITHER pipeline table. This is the row the old
        # pipeline-only sweep could not see, and the reason D1 exists.
        cls.catalogue_only = make_event("D1 - CATALOGUE-ONLY",
                                        sales_executive=cls.exec_user)
        # In the catalogue AND carrying a review, so scope=pipeline has something
        # to report and the narrowing test is not vacuous.
        cls.pipeline_event = make_event("D1 - PIPELINE",
                                       sales_executive=cls.exec_user)
        PaperReview.objects.create(
            event_code="D1 - PIPELINE", speaker_name="S", email="s@example.com",
            paper_submission_date=date(2026, 5, 1))
        # No sales_executive, but a speaker_sales assignee WITH an email: the
        # DEGRADED case. 11 of the 142 real events look like this, and nothing
        # tested it before.
        cls.degraded_event = make_event("D1 - DEGRADED", cc_users=[cls.cc_user])
        PaperReview.objects.create(
            event_code="D1 - DEGRADED", speaker_name="T", email="t@example.com",
            paper_submission_date=date(2026, 5, 1))

    def test_default_scope_is_the_whole_catalogue(self):
        """The default must be `all` — a report that has to be asked for the
        catalogue is a report whose headline number is a sample."""
        out = run()
        self.assertIn("D1 - CATALOGUE-ONLY", out)
        self.assertIn("the whole Event catalogue", out)

    def test_scope_pipeline_genuinely_narrows(self):
        """
        Proves the two scopes differ. Without this, `all` could silently BE
        `pipeline` and every other test here would still pass.
        """
        out = run("--scope", "pipeline")
        self.assertNotIn("D1 - CATALOGUE-ONLY", out)
        self.assertIn("D1 - PIPELINE", out)

    def test_a_degraded_event_is_reported_degraded_not_fallback(self):
        """
        sales_executive is null, so resolve_recipients promotes the fixed Cc to
        To and the send still lands. Reporting that as FALLBACK would overstate
        the problem; reporting it as RESOLVED would hide a missing assignment.
        """
        out = run("--only", "degraded")
        self.assertIn("D1 - DEGRADED", out)
        self.assertIn("DEGRADED", out)
        # The fixed Cc became the To, and the reason is named on the row.
        self.assertIn(REPORT_CC_JOINED, out)
        self.assertIn("no_sales_executive", out)
        # The assigned speaker_sales user is no longer a recipient of any kind.
        self.assertNotIn("d1_cc@example.com", out)

    def test_degraded_is_counted_separately_from_the_other_outcomes(self):
        out = run()
        self.assertEqual(readiness_count(out, "degraded"), 1)
        self.assertEqual(readiness_count(out, "resolved"), 2)
        self.assertEqual(readiness_count(out, "fallback"), 0)
        self.assertEqual(readiness_count(out, "event_not_found"), 0)

    def test_readiness_counts_come_from_the_catalogue_not_the_pipeline(self):
        out = run()
        self.assertEqual(readiness_count(out, "events in catalogue"), 3)
        self.assertEqual(readiness_count(out, "with a sales_executive"), 2)
        self.assertEqual(readiness_count(out, "without one"), 1)

    def test_the_fixed_cc_is_named_in_the_readiness_block(self):
        """It is now the only Cc, so a report omitting it hides half the send."""
        self.assertIn(REPORT_CC_JOINED, run())

    def test_readiness_is_unaffected_by_the_scope_flag(self):
        """
        The catalogue counts describe the catalogue. Narrowing which codes are
        LISTED must not change the denominator the verdict is computed from.
        """
        for scope in ("all", "pipeline"):
            with self.subTest(scope=scope):
                out = run("--scope", scope)
                self.assertEqual(readiness_count(out, "events in catalogue"), 3)
                self.assertEqual(readiness_count(out, "with a sales_executive"), 2)

    def test_only_filters_the_listing_but_not_the_summary(self):
        out = run("--only", "resolved")
        self.assertNotIn("D1 - DEGRADED", out.split("Event catalogue readiness")[0])
        # The summary still counts every outcome.
        self.assertEqual(readiness_count(out, "degraded"), 1)
        self.assertIn("of 3 shown", out)

    def test_csv_covers_the_catalogue_only_code_too(self):
        import csv
        from io import StringIO as SIO
        rows = list(csv.DictReader(SIO(run("--format", "csv"))))
        by_code = {r["event_code"]: r for r in rows}
        self.assertIn("D1 - CATALOGUE-ONLY", by_code)
        self.assertEqual(by_code["D1 - DEGRADED"]["outcome"], "degraded")
        self.assertEqual(by_code["D1 - DEGRADED"]["to"], REPORT_CC_JOINED)
        self.assertEqual(by_code["D1 - PIPELINE"]["cc"], REPORT_CC_JOINED)

    def test_still_sends_and_writes_nothing_at_catalogue_scope(self):
        """D1 widened the sweep; the read-only guarantee must widen with it."""
        before = (Event.objects.count(), PaperReview.objects.count(),
                  ProposalSubmission.objects.count())
        run("--scope", "all")
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(NotificationLog.objects.count(), 0)
        self.assertEqual(
            (Event.objects.count(), PaperReview.objects.count(),
             ProposalSubmission.objects.count()), before)


@override_settings(PAPER_REVIEW_CC_EMAILS=REPORT_CC)
class ReadinessVerdictTests(TestCase):
    """
    D1 asks for the conclusion to be STATED — "if most events are null, say so
    plainly". Each branch is pinned, because the verdict is the sentence somebody
    will act on and a silently-wrong threshold reads as a clean bill of health.

    Fixtures are built per test rather than in setUpTestData: the verdict is a
    percentage of the whole catalogue, so each branch needs its own catalogue.
    """

    def test_no_sales_executive_anywhere_is_stated_plainly(self):
        make_event("V - NONE-1")
        make_event("V - NONE-2")
        out = run()
        self.assertIn("VERDICT", out)
        self.assertIn("NO event has a sales_executive", out)
        self.assertIn("needs revisiting", out)
        self.assertEqual(readiness_count(out, "with a sales_executive"), 0)

    def test_a_minority_with_a_sales_executive_warns(self):
        exec_user = make_user("v_exec_min", "sales")
        make_event("V - MIN-1", sales_executive=exec_user)
        for i in range(3):
            make_event(f"V - MIN-GAP-{i}")
        out = run()
        self.assertIn("VERDICT", out)
        self.assertIn("1/4", out)
        self.assertIn("25%", out)
        self.assertIn("missing for the majority", out)

    def test_a_majority_with_a_sales_executive_reports_the_ratio(self):
        exec_user = make_user("v_exec_maj", "sales")
        for i in range(3):
            make_event(f"V - MAJ-{i}", sales_executive=exec_user)
        make_event("V - MAJ-GAP")
        out = run()
        self.assertIn("VERDICT", out)
        self.assertIn("3/4", out)
        self.assertIn("75%", out)
        # Must be the plain-ratio branch, NOT either alarm branch. Asserting the
        # ratio alone is not enough: the warning branch prints the same ratio, so
        # a threshold moved up to 90 would still satisfy it.
        self.assertNotIn("needs revisiting", out)
        self.assertNotIn("missing for the majority", out)
        self.assertNotIn("revisit the recipient design", out)

    def test_exactly_half_is_not_reported_as_a_minority(self):
        """
        The boundary. `< 50` is the warning branch, so 50% must not warn — a
        threshold that fires ON the boundary would call an even split a majority
        failure.
        """
        exec_user = make_user("v_exec_half", "sales")
        make_event("V - HALF-1", sales_executive=exec_user)
        make_event("V - HALF-2")
        out = run()
        self.assertIn("1/2", out)
        self.assertNotIn("missing for the majority", out)

    def test_an_event_without_a_sales_executive_is_counted_as_such(self):
        make_event("V - BARE")
        out = run()
        self.assertEqual(readiness_count(out, "without one"), 1)
        # It degrades to the fixed Cc rather than reaching the watchdog.
        self.assertEqual(readiness_count(out, "degraded"), 1)
        self.assertEqual(readiness_count(out, "fallback"), 0)

    @override_settings(PAPER_REVIEW_CC_EMAILS=[])
    def test_the_fallback_warning_names_the_alert_address_setting(self):
        make_event("V - FALLBACK")
        out = run()
        self.assertIn("PAPER_REVIEW_ALERT_EMAIL", out)
        self.assertIn("PAPER_REVIEW_NOTIFICATIONS_ENABLED", out)
