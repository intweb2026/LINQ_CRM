"""
accounts/tests_server_filter_coverage.py
─────────────────────────────────────────
Every column of every SERVER-MODE table filters over the whole table.

THE FAILURE THIS EXISTS TO CATCH
DataTable is the only thing that decides where a filter runs
(frontend/src/lib/filterSpec.js partitionConds), and it decides on one fact:
whether the column declares `serverField` AND the resource's registry lists that
field. If either is missing the condition is not rejected — it is re-applied in
the browser, over the rows that happen to have been fetched, and the footer
counts those. A filter that answers from the current scroll position while
looking exactly like a working filter is the worst shape this table can take,
and it is the SILENT one: nothing errors, nothing logs, and the numbers are
merely wrong.

It had gone silent in two ways at once. Paper Review and Proposal Submission
declared no `serverField` on a single column, under a comment asserting that
their filters were "evaluated by the database over every row". And every table's
filter panel opened each column on the "Contains" operator, which the backend's
`choice` vocabulary did not have — so ticking a payment status, a grade or a
priority fell back to the browser on tables where every other part of the
plumbing was correct.

So this file asserts BOTH halves, from the JSX itself:

  1. every column in a server-mode table declares `serverField`
  2. every name so declared is registered on that resource's viewset

Reading the JSX is deliberate. A test that only checked the Python registries
would have passed throughout the entire period Paper Review filtered nothing
server-side, because those registries were complete the whole time — the
frontend simply never named them.

    python manage.py test accounts.tests_server_filter_coverage
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

from book_delegate.views import BookDelegateViewSet
from paper_review.views import PaperReviewViewSet
from proposal_submission.views import ProposalSubmissionViewSet
from ticket_central.views import TicketViewSet
from webhooks.views import WebhookLogViewSet

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"

# (label, jsx file, first line of the column list, first line after it, viewset,
#  columns that legitimately have no server field)
#
# The exemptions are the only two columns in the app that are not data: a button
# that starts a server action, and the unlabelled retry cell. Neither holds a
# value, so neither can be filtered by any means, server or browser. Every other
# column must be filterable over the whole table — add to this list only with a
# reason of the same kind.
TABLES = [
    (
        "Bookings", "pages/BookingsPage.jsx",
        "const bkCols", "const TAB_STATUSES",
        BookDelegateViewSet, {"transfer"},
    ),
    (
        "Paper Review", "pages/PaperReviewPage.jsx",
        "const REVIEW_COLS", "const REVIEW_GROUPS",
        PaperReviewViewSet, set(),
    ),
    (
        "Proposal Submission", "pages/ProposalSubmissionPage.jsx",
        "const PROPOSAL_COLS", "const PROPOSAL_GROUPS",
        ProposalSubmissionViewSet, set(),
    ),
    (
        "Ticket Central", "pages/TicketCentralPage.jsx",
        "const tkCols", "const TK_COLS",
        TicketViewSet, set(),
    ),
    (
        "Webhook Logs", "pages/webhooks/Logs.jsx",
        "cols={[", "        ]}",
        WebhookLogViewSet, {"_a"},
    ),
]

# `{ key: 'x', ... }` at the head of a column definition. Anchored on the brace
# so it cannot match the `key: c.key` inside a .map(), which carries its own
# serverField and is checked by eye rather than by this regex.
COL_RE = re.compile(r"^\s*\{ key: '([A-Za-z0-9_]+)',(.*)$")
SERVER_FIELD_RE = re.compile(r"serverField: '([A-Za-z0-9_]+)'")


def column_lines(jsx, start, end):
    """The lines of one column list, by the markers that bound it."""
    lines = jsx.split("\n")
    lo = next(i for i, l in enumerate(lines) if start in l)
    hi = next(i for i, l in enumerate(lines[lo + 1:], lo + 1) if end in l)
    return lines[lo:hi]


class ServerFilterCoverageTests(SimpleTestCase):
    def _table(self, label):
        return next(t for t in TABLES if t[0] == label)

    def test_every_column_declares_a_server_field(self):
        for label, path, start, end, _viewset, exempt in TABLES:
            with self.subTest(table=label):
                jsx = (FRONTEND / path).read_text(encoding="utf-8")
                missing = []
                for line in column_lines(jsx, start, end):
                    m = COL_RE.match(line)
                    if not m:
                        continue
                    key, rest = m.group(1), m.group(2)
                    if key in exempt or SERVER_FIELD_RE.search(rest):
                        continue
                    missing.append(key)
                self.assertEqual(
                    missing, [],
                    f"\n{label} ({path}): {len(missing)} column(s) declare no "
                    f"serverField, so a filter on them is applied in the BROWSER "
                    f"over the loaded rows only:\n  " + "\n  ".join(missing) +
                    "\n\nRegister the field on the viewset's filter_spec_fields "
                    "and name it here, or add it to this table's exemptions with "
                    "a reason it holds no filterable value.",
                )

    def test_every_declared_field_is_registered_on_the_viewset(self):
        """
        Deny-by-default means an unregistered name does not 400 — it silently
        drops back to the browser, which is exactly the failure this file is
        about. So the two lists have to be compared explicitly.
        """
        for label, path, start, end, viewset, _exempt in TABLES:
            with self.subTest(table=label):
                jsx = (FRONTEND / path).read_text(encoding="utf-8")
                registered = set(viewset.filter_spec_fields)
                unknown = sorted({
                    m.group(1)
                    for line in column_lines(jsx, start, end)
                    for m in [SERVER_FIELD_RE.search(line)] if m
                } - registered)
                self.assertEqual(
                    unknown, [],
                    f"\n{label} ({path}) names filter fields that "
                    f"{viewset.__name__}.filter_spec_fields does not register: "
                    f"{unknown}. The schema endpoint would not advertise them, so "
                    f"every condition on those columns would be evaluated in the "
                    f"browser over the loaded page.",
                )

    def test_the_criteria_columns_are_wired_too(self):
        """
        Paper Review's six rubric columns are generated by a .map() rather than
        written out, so the regex above cannot see them and they were the easiest
        six to leave behind.
        """
        jsx = (FRONTEND / "pages/PaperReviewPage.jsx").read_text(encoding="utf-8")
        self.assertIn(
            "key: c.key, serverField: c.key,", jsx,
            "PAPER_REVIEW_CRITERIA columns no longer declare serverField — the six "
            "rubric scores would filter over the loaded page only.",
        )


class FilterDefaultOperatorTests(SimpleTestCase):
    """
    The default operator a column's filter opens on has to have a backend form,
    or the most common interaction in the table is a client-side filter.
    """

    def test_choice_columns_open_on_is_not_contains(self):
        src = (FRONTEND / "components/DataTable.jsx").read_text(encoding="utf-8")
        self.assertIn(
            "op: col.opts ? 'Is' : 'Contains'", src,
            "DataTable's blank condition no longer distinguishes a closed-list "
            "column. Opening every column on Contains put every status/grade/"
            "priority filter back in the browser.",
        )
        self.assertNotIn(
            "{ key: col.key, op: 'Contains', values: [] }", src,
            "A blank condition is still being built inline with a hardcoded "
            "Contains; route it through blankCond(col) so the choice-column rule "
            "applies everywhere a filter can be opened.",
        )

    def test_the_backend_accepts_both_operators_on_a_choice_field(self):
        """
        Filters saved before the default changed still carry Contains, and a
        stored filter that quietly stops reaching the database is the same bug
        wearing a different hat.
        """
        from accounts.filter_spec import OPERATORS_BY_TYPE
        for op in ("is", "any_of", "contains", "not_contains", "like"):
            self.assertIn(op, OPERATORS_BY_TYPE["choice"], f"choice lost '{op}'")
