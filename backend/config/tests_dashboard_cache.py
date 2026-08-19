"""
config/tests_dashboard_cache.py
────────────────────────────────
The dashboard aggregate response cache.

WHAT THIS PINS
1.  Two identical requests do the database work ONCE. That is the whole point.
2.  Two users with DIFFERENT RBAC SCOPES get different keys and different
    payloads. This is the dangerous half: the view cannot use cache_page
    precisely because the response varies by scope, and a key that ignored scope
    would serve one user's numbers to another. A cache bug here is a data leak,
    not a stale number.
3.  An unknown period is a 400 and is never cached — the validation happens
    before the cache is touched, so a bad key can neither be written nor served.

The cache is cleared in setUp: LocMemCache is per-PROCESS, so an entry written
by one test is visible to the next and these assertions would pass or fail
depending on execution order.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from config.views import DashboardAggregateView
from events.models import Event
from teams.models import Team

User = get_user_model()
URL = "/api/stats/dashboard_aggregate/"
VIEW = DashboardAggregateView.as_view()


def get(user, query=""):
    req = APIRequestFactory().get(URL + query)
    force_authenticate(req, user=user)
    resp = VIEW(req)
    resp.render()
    return resp


class DashboardCacheTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.all_access = Team.objects.create(name="dash_admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="dash_admin_u", password="x", role="admin",
            email="dashadmin@iq-hub.com",
        )
        cls.admin.team = cls.all_access
        cls.admin.save()

        for code in ("DASH - AA", "DASH - BB"):
            Event.objects.create(
                event_code=code, name=f"Event {code}", event_date="2026-01-01",
            )
            inv = BookEvent.objects.create(
                invoice_number=f"DASH-{code[-2:]}", event_code=code,
                request_date="2026-01-01", payment_status="Paid",
            )
            BookDelegate.objects.create(
                invoice=inv, event_code=code,
                first_name="X", email=f"x{code[-2:]}@example.com",
            )

    def setUp(self):
        cache.clear()

    # ── 1. The cache hits ─────────────────────────────────────────────────────

    def test_two_identical_requests_run_the_aggregate_once(self):
        with CaptureQueriesContext(connection) as first:
            r1 = get(self.admin)
        self.assertEqual(r1.status_code, 200, r1.content)

        with CaptureQueriesContext(connection) as second:
            r2 = get(self.admin)
        self.assertEqual(r2.status_code, 200, r2.content)

        self.assertGreater(len(first.captured_queries), 1)
        self.assertEqual(
            len(second.captured_queries), 0,
            f"a cache hit should issue no queries, got "
            f"{[q['sql'][:80] for q in second.captured_queries]}",
        )

    def test_the_cached_payload_is_identical(self):
        first = get(self.admin).data
        second = get(self.admin).data
        self.assertEqual(first, second)
        # cached_at is stamped when the entry is BUILT, so a hit carries the
        # original timestamp. That is what makes staleness diagnosable.
        self.assertEqual(first["cached_at"], second["cached_at"])

    def test_cached_at_is_present_and_is_a_string(self):
        body = get(self.admin).data
        self.assertIn("cached_at", body)
        self.assertIsInstance(body["cached_at"], str)

    def test_a_different_period_is_a_different_entry(self):
        get(self.admin)
        with CaptureQueriesContext(connection) as ctx:
            r = get(self.admin, "?period=last_30_days")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertGreater(
            len(ctx.captured_queries), 0,
            "a different period reused another period's cache entry",
        )

    # ── 2. Scope isolation ────────────────────────────────────────────────────

    def test_two_scopes_produce_different_payloads_and_do_not_share_an_entry(self):
        """
        The leak test. A scoped user must never read the all-access user's
        numbers out of the cache, and vice versa.
        """
        scoped_team = Team.objects.create(name="dash_scoped", is_all_access=False)
        scoped = User.objects.create_user(
            username="dash_scoped_u", password="x", role="sales",
            email="dashscoped@iq-hub.com",
        )
        scoped.team = scoped_team
        scoped.save()

        admin_body = get(self.admin).data

        # Warm, then measure: the scoped user must still do real work, which
        # proves the key differs rather than merely that the payload differs.
        with CaptureQueriesContext(connection) as ctx:
            scoped_resp = get(scoped)
        self.assertEqual(scoped_resp.status_code, 200, scoped_resp.content)
        self.assertGreater(
            len(ctx.captured_queries), 0,
            "the scoped user was served the admin's cache entry",
        )

        scoped_body = scoped_resp.data
        self.assertNotEqual(
            admin_body["outstanding"], scoped_body["outstanding"],
            "an all-access and a no-access user reported the same totals; "
            "either scoping or the cache key is wrong",
        )

    def test_each_scope_caches_independently(self):
        scoped_team = Team.objects.create(name="dash_scoped2", is_all_access=False)
        scoped = User.objects.create_user(
            username="dash_scoped_u2", password="x", role="sales",
            email="dashscoped2@iq-hub.com",
        )
        scoped.team = scoped_team
        scoped.save()

        get(self.admin)
        get(scoped)
        with CaptureQueriesContext(connection) as ctx:
            get(self.admin)
        self.assertEqual(
            len(ctx.captured_queries), 0,
            "the scoped request evicted or overwrote the admin's entry",
        )

    # ── 3. Bad input is never cached ──────────────────────────────────────────

    def test_unknown_period_is_400(self):
        resp = get(self.admin, "?period=not-a-period")
        self.assertEqual(resp.status_code, 400)

    def test_unknown_period_is_not_cached_and_does_not_poison_the_default(self):
        self.assertEqual(get(self.admin, "?period=not-a-period").status_code, 400)
        # Twice: a cached 400 would come back without touching the database, and
        # would also mean a bad key had been written.
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(get(self.admin, "?period=not-a-period").status_code, 400)
        self.assertEqual(
            len(ctx.captured_queries), 0,
            "validation should short-circuit before any query, cached or not",
        )
        # And the real request still works and still does its work.
        with CaptureQueriesContext(connection) as ctx2:
            ok = get(self.admin)
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertGreater(len(ctx2.captured_queries), 0)


class OwnerMapCacheTests(TestCase):
    """_owner_by_event's cache, including that what it stores is picklable."""

    @classmethod
    def setUpTestData(cls):
        for i in range(3):
            Event.objects.create(
                event_code=f"OWN - {i:02d}", name=f"Owned {i}",
                event_date="2026-01-01",
            )

    def setUp(self):
        cache.clear()

    def users_qs(self):
        return User.objects.filter(is_active=True)

    def test_second_call_is_served_from_cache(self):
        DashboardAggregateView._owner_by_event(self.users_qs())
        with CaptureQueriesContext(connection) as ctx:
            DashboardAggregateView._owner_by_event(self.users_qs())
        # users.count() and Event.objects.count() build the key, so a hit is two
        # cheap COUNTs and nothing else — never the catalogue walk.
        catalogue = [q["sql"] for q in ctx.captured_queries
                     if "events" in q["sql"] and "COUNT" not in q["sql"].upper()]
        self.assertEqual(catalogue, [], "the catalogue was walked again on a cache hit")

    def test_adding_a_user_invalidates_the_entry(self):
        """
        The key carries the active user count precisely so that adding a user
        takes effect without a process restart, which is the case
        accounts/user_resolution.py documents as needing exactly that.
        """
        DashboardAggregateView._owner_by_event(self.users_qs())
        User.objects.create_user(
            username="own_new", password="x", role="sales",
            email="ownnew@iq-hub.com",
        )
        with CaptureQueriesContext(connection) as ctx:
            DashboardAggregateView._owner_by_event(self.users_qs())
        self.assertGreater(
            len(ctx.captured_queries), 2,
            "adding a user did not invalidate the owner-map cache",
        )

    def test_what_is_cached_is_picklable(self):
        """
        LocMem pickles on set and Redis pickles on the wire. A Counter, a
        queryset or a model instance in diagnostics would raise here rather
        than in production.
        """
        import pickle
        result = DashboardAggregateView._owner_by_event(self.users_qs())
        round_tripped = pickle.loads(pickle.dumps(result))
        self.assertEqual(round_tripped, result)

        owner, diagnostics = result
        self.assertIsInstance(owner, dict)
        self.assertIsInstance(diagnostics, dict)
        self.assertIn("name_resolution", diagnostics)
        # report() promises plain data; hold it to that.
        self.assertIsInstance(diagnostics["name_resolution"], dict)
        self.assertIsInstance(
            diagnostics["name_resolution"]["unresolved_values"], list)
