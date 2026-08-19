"""
ticket_central/tests_search_fields.py
──────────────────────────────────────
The trimmed search_fields list, and the invariant that keeps it fast.

WHY THIS IS A TEST
Ticket search was fourteen unanchored substring predicates per row over ~43,000
rows. It is now seven, each backed by an expression GIN index on UPPER(col),
which is what Django's __icontains actually compiles to on PostgreSQL.

THE INVARIANT: search_fields and the trigram indexes must stay THE SAME SET.
SearchFilter ORs one predicate per field. The planner can only bitmap-OR the
whole disjunction if EVERY branch is indexed — a single uncovered field drags
the entire search back to a sequential scan, and nothing anywhere would report
it. Adding a field to search_fields therefore means adding its GinIndex in the
same change, and this test is what fails if someone adds only one of the two.

NO EXPLAIN ASSERTION HERE, DELIBERATELY. An EXPLAIN against an empty test
database proves nothing: PostgreSQL will pick a sequential scan on a table with
no rows no matter how it is indexed. The plan evidence belongs in the
workstream report, taken against real data. What is testable here is the
structural invariant, and that is what this file covers.
"""
from django.test import TestCase

from ticket_central.models import Ticket
from ticket_central.views import TicketViewSet

EXPECTED_SEARCH_FIELDS = [
    "ticket_number", "event_code", "purpose", "organizer",
    "competitor_event_name", "assigned_mr", "assign_name",
]

REMOVED_FROM_SEARCH = [
    "type_of_ticket", "ticket_type", "mr_comments", "dm_comments",
    "assign_name_lx2", "linkedin_keywords", "event_location",
]


class SearchFieldsTests(TestCase):

    def test_search_fields_is_the_retained_seven(self):
        self.assertEqual(list(TicketViewSet.search_fields), EXPECTED_SEARCH_FIELDS)

    def test_every_removed_field_is_still_reachable_through_the_filter_spec(self):
        """
        Removing a field from BOTH search_fields and filter_spec_fields would
        make that column unreachable from the UI entirely. The compound filter
        engine is where these now live, and it is explicit about the column it
        scans rather than folding it into a fourteen-way OR.
        """
        spec = TicketViewSet.filter_spec_fields
        keys = set(spec) if isinstance(spec, dict) else {f["name"] for f in spec}
        missing = [f for f in REMOVED_FROM_SEARCH if f not in keys]
        self.assertEqual(
            missing, [],
            f"{missing} was dropped from search_fields and is absent from "
            f"filter_spec_fields, so it is now unreachable from the UI",
        )

    def test_every_search_field_has_a_trigram_index(self):
        """
        THE INVARIANT. One uncovered field in the OR is a sequential scan over
        the whole table, silently.
        """
        from django.contrib.postgres.indexes import GinIndex

        indexed = set()
        for index in Ticket._meta.indexes:
            if not isinstance(index, GinIndex):
                continue
            for expression in index.expressions:
                # OpClass wraps the Upper(...) expression; its source is the
                # Upper, whose own source is the bare column reference.
                for node in expression.flatten():
                    name = getattr(node, "name", None)
                    if name:
                        indexed.add(name)

        missing = [f for f in TicketViewSet.search_fields if f not in indexed]
        self.assertEqual(
            missing, [],
            f"{missing} is searched but has no trigram index, which forces a "
            f"sequential scan for the whole search",
        )

    def test_no_trigram_index_is_left_orphaned(self):
        """
        The other direction. An index on a field nobody searches is dead weight
        on every write, which is the cost this workstream measured and accepted
        only for the fields that are actually searched.
        """
        from django.contrib.postgres.indexes import GinIndex

        indexed = set()
        for index in Ticket._meta.indexes:
            if isinstance(index, GinIndex):
                for expression in index.expressions:
                    for node in expression.flatten():
                        name = getattr(node, "name", None)
                        if name:
                            indexed.add(name)

        orphans = sorted(indexed - set(TicketViewSet.search_fields))
        self.assertEqual(
            orphans, [],
            f"trigram index(es) exist for {orphans}, which nothing searches",
        )

    def test_the_expensive_prose_columns_are_no_longer_searched(self):
        """Named explicitly: these two TextFields were the costly pair."""
        self.assertNotIn("mr_comments", TicketViewSet.search_fields)
        self.assertNotIn("dm_comments", TicketViewSet.search_fields)
