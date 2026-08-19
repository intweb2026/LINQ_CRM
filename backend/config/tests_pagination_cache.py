"""
config/tests_pagination_cache.py
─────────────────────────────────
CachedCountPaginator: the COUNT(*) is memoised, the memoisation is keyed
correctly, and the response body did not change shape.

WHY THE COUNT IS WORTH CACHING
DRF reads paginator.count on EVERY page of a listing, so scrolling a large table
pays one identical COUNT(*) per page, and every 30-second background poll pays
another. The number cannot differ between pages of the same filtered query.

WHAT THESE TESTS PIN, AND WHY EACH ONE MATTERS
1.  The cache actually hits. Without this the class is dead weight.
2.  The KEY separates different filters. This is the dangerous half: a key that
    collided across filter_specs would report one filter's total under another,
    which is a wrong number rather than a slow one, and RBAC scoping rides in
    the same SQL, so a collision there would leak one user's row count to
    another.
3.  Small results are not cached at all. Below MIN_ROWS_TO_CACHE the COUNT is
    cheaper than the round trip to remember it, and every list in the app is
    small on a fresh database — caching those would be pure overhead plus a
    staleness window bought for nothing.
4.  The seven response keys are unchanged. The frontend reads all of them, and
    this workstream is not allowed to alter any endpoint's response shape.

The cache is cleared in setUp: LocMemCache is per-PROCESS, not per-test, so
without this an entry written by one test is visible to the next and the
"issues its own COUNT" assertions fail depending on execution order.
"""
from datetime import date

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from config.pagination import CachedCountPaginator, StandardPagination
from events.models import Event


def count_queries(ctx):
    """Just the COUNT(*) statements captured, in order."""
    return [
        q["sql"] for q in ctx.captured_queries
        if q["sql"].lstrip().upper().startswith("SELECT COUNT")
    ]


class CachedCountPaginatorTests(TestCase):
    """
    Fixtures split across two statuses so there is a real filter to key on, and
    the two groups are deliberately DIFFERENT sizes — equal counts would let a
    key collision pass test 2 by returning the right number for the wrong
    reason.
    """

    @classmethod
    def setUpTestData(cls):
        Event.objects.bulk_create([
            Event(
                event_code=f"LIVE-{i:04d}",
                name=f"Live event {i}",
                status=Event.Status.LIVE,
                event_date=date(2026, 8, 19),
            )
            for i in range(12)
        ])
        Event.objects.bulk_create([
            Event(
                event_code=f"DRAFT-{i:04d}",
                name=f"Draft event {i}",
                status=Event.Status.DRAFT,
                event_date=date(2026, 8, 19),
            )
            for i in range(7)
        ])

    def setUp(self):
        cache.clear()

    # A fresh queryset per call. Reusing one object would let Django's own
    # result cache answer the second .count() from memory and the test would
    # pass without the paginator cache existing at all.
    def live(self):
        return Event.objects.filter(status=Event.Status.LIVE)

    def draft(self):
        return Event.objects.filter(status=Event.Status.DRAFT)

    # ── 1. The cache hits ─────────────────────────────────────────────────────

    def test_same_filter_twice_issues_exactly_one_count(self):
        """
        Two paginators over the same filtered queryset — which is what two
        requests for the same page are — must go to the database for the COUNT
        once. MIN_ROWS_TO_CACHE is lowered because the point under test is the
        caching, not the threshold; the threshold has its own test below.
        """
        original = CachedCountPaginator.MIN_ROWS_TO_CACHE
        CachedCountPaginator.MIN_ROWS_TO_CACHE = 1
        try:
            with CaptureQueriesContext(connection) as ctx:
                first = CachedCountPaginator(self.live(), 50).count
                second = CachedCountPaginator(self.live(), 50).count
        finally:
            CachedCountPaginator.MIN_ROWS_TO_CACHE = original

        self.assertEqual(first, 12)
        self.assertEqual(second, 12)
        self.assertEqual(
            len(count_queries(ctx)), 1,
            "the second paginator should have been served from the cache",
        )

    def test_ordering_does_not_change_the_key(self):
        """
        The key strips ORDER BY. Ordering cannot change a count but does change
        the SQL text, so without the strip the same filter under two sorts would
        hold two entries and the second sort would miss.
        """
        original = CachedCountPaginator.MIN_ROWS_TO_CACHE
        CachedCountPaginator.MIN_ROWS_TO_CACHE = 1
        try:
            with CaptureQueriesContext(connection) as ctx:
                CachedCountPaginator(self.live().order_by("event_code"), 50).count
                CachedCountPaginator(self.live().order_by("-event_date"), 50).count
        finally:
            CachedCountPaginator.MIN_ROWS_TO_CACHE = original

        self.assertEqual(len(count_queries(ctx)), 1)

    # ── 2. The key separates different filters ────────────────────────────────

    def test_different_filters_each_issue_their_own_count(self):
        """
        Two different filters must never share an entry. Asserted on the VALUES
        as well as the query count: 12 and 7 are the two group sizes, so a
        collision shows up as the second call returning 12.
        """
        original = CachedCountPaginator.MIN_ROWS_TO_CACHE
        CachedCountPaginator.MIN_ROWS_TO_CACHE = 1
        try:
            with CaptureQueriesContext(connection) as ctx:
                live = CachedCountPaginator(self.live(), 50).count
                draft = CachedCountPaginator(self.draft(), 50).count
        finally:
            CachedCountPaginator.MIN_ROWS_TO_CACHE = original

        self.assertEqual(live, 12)
        self.assertEqual(draft, 7, "a shared cache key would return 12 here")
        self.assertEqual(len(count_queries(ctx)), 2)

    # ── 3. Small results are never cached ─────────────────────────────────────

    def test_results_below_the_threshold_are_not_cached(self):
        """
        MIN_ROWS_TO_CACHE is 2000 and these fixtures are 12 rows, so at the real
        threshold nothing should be written and both paginators should count for
        themselves. Observed as two COUNTs, which is the behaviour that matters;
        asserting on the derived key would only restate the implementation.
        """
        self.assertEqual(CachedCountPaginator.MIN_ROWS_TO_CACHE, 2000)

        with CaptureQueriesContext(connection) as ctx:
            first = CachedCountPaginator(self.live(), 50).count
            second = CachedCountPaginator(self.live(), 50).count

        self.assertEqual(first, 12)
        self.assertEqual(second, 12)
        self.assertEqual(
            len(count_queries(ctx)), 2,
            "a 12-row result is below MIN_ROWS_TO_CACHE and must not be cached",
        )

    def test_a_queryset_that_cannot_render_sql_falls_back_to_a_plain_count(self):
        """
        The except branch must return a correct number rather than propagate.
        Simulated with an object_list that raises on .query, which is what an
        exotic values/union queryset does in the wild — the paginator has to
        survive it, because a listing failing outright is far worse than an
        uncached count.
        """
        class Unrenderable(list):
            @property
            def query(self):
                raise TypeError("cannot compile")

        paginator = CachedCountPaginator(Unrenderable([1, 2, 3]), 50)
        # order_by() is called on object_list before .query is reached, so the
        # fallback is entered via AttributeError for a plain list; either way the
        # contract is the same, a correct count and no exception.
        self.assertEqual(paginator.count, 3)


class PaginatedResponseShapeTests(TestCase):
    """
    The seven keys, their types, and their values. This is the test that proves
    the workstream did not change any endpoint's response shape.
    """

    @classmethod
    def setUpTestData(cls):
        Event.objects.bulk_create([
            Event(
                event_code=f"SHAPE-{i:04d}",
                name=f"Event {i}",
                status=Event.Status.LIVE,
                event_date=date(2026, 8, 19),
            )
            for i in range(120)
        ])

    def setUp(self):
        cache.clear()

    def paginate(self, query=""):
        paginator = StandardPagination()
        request = Request(APIRequestFactory().get(f"/api/events/{query}"))
        page = paginator.paginate_queryset(
            Event.objects.all().order_by("event_code"), request
        )
        return paginator.get_paginated_response(
            [{"id": e.pk, "event_code": e.event_code} for e in page]
        ).data

    def test_all_seven_keys_present_with_the_right_types(self):
        body = self.paginate()

        self.assertEqual(
            set(body),
            {"count", "total_pages", "page", "page_size", "next", "previous",
             "results"},
        )
        self.assertIsInstance(body["count"], int)
        self.assertIsInstance(body["total_pages"], int)
        self.assertIsInstance(body["page"], int)
        self.assertIsInstance(body["page_size"], int)
        self.assertIsInstance(body["results"], list)
        # next/previous are a URL or null, never absent.
        self.assertTrue(body["next"] is None or isinstance(body["next"], str))
        self.assertTrue(
            body["previous"] is None or isinstance(body["previous"], str)
        )

    def test_first_page_values(self):
        body = self.paginate()

        self.assertEqual(body["count"], 120)
        self.assertEqual(body["total_pages"], 3)      # 120 over a page_size of 50
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["page_size"], 50)
        self.assertEqual(len(body["results"]), 50)
        self.assertIsNone(body["previous"])
        self.assertIsNotNone(body["next"])

    def test_middle_page_values(self):
        body = self.paginate("?page=2")

        self.assertEqual(body["count"], 120)
        self.assertEqual(body["page"], 2)
        self.assertIsNotNone(body["previous"])
        self.assertIsNotNone(body["next"])

    def test_page_size_query_param_is_honoured_and_reported(self):
        body = self.paginate("?page_size=25")

        self.assertEqual(body["page_size"], 25)
        self.assertEqual(body["total_pages"], 5)
        self.assertEqual(len(body["results"]), 25)

    def test_the_cached_count_does_not_change_the_reported_total(self):
        """
        The cached path and the uncached path must report the same number. Run
        with the threshold lowered so the second call is definitely a cache hit,
        which is the configuration where a stale or mis-keyed entry would show
        up as a changed total.
        """
        original = CachedCountPaginator.MIN_ROWS_TO_CACHE
        CachedCountPaginator.MIN_ROWS_TO_CACHE = 1
        try:
            first = self.paginate()
            second = self.paginate("?page=2")
        finally:
            CachedCountPaginator.MIN_ROWS_TO_CACHE = original

        self.assertEqual(first["count"], 120)
        self.assertEqual(second["count"], 120)
        self.assertEqual(first["total_pages"], second["total_pages"])
