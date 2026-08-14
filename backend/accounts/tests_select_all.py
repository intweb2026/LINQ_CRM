"""
accounts/tests_select_all.py
─────────────────────────────
FilterSpecMixin's `ids` action — the endpoint the table's select-all is built on.

WHAT THIS IS GUARDING
The header checkbox used to tick one page. On a filter matching 35,690 tickets,
"select all" selected 50 and every bulk action ran against those 50 — reporting
success, because 50 rows really were updated. Nothing about that was visible from
the UI, and no test caught it, because selecting a page is exactly what the code
did on purpose.

So the invariant worth locking down is not "the endpoint returns some ids". It is
that `ids` and `list` answer about THE SAME ROWS. Every test below that matters
compares the two directly rather than asserting a hand-counted number: a filter
the list applies and this does not is a mass update pointed at rows the user
never saw, and it is the only way this endpoint can be dangerously wrong.

    python manage.py test accounts.tests_select_all
"""
import json
from datetime import date, timedelta
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from events.models import Event
from teams.models import Team
from ticket_central.models import Ticket
from ticket_central.views import TicketViewSet

User = get_user_model()

DELEGATE_IDS = BookDelegateViewSet.as_view({"get": "ids"})
DELEGATE_LIST = BookDelegateViewSet.as_view({"get": "list"})
TICKET_IDS = TicketViewSet.as_view({"get": "ids"})
TICKET_LIST = TicketViewSet.as_view({"get": "list"})

IN_SCOPE = "SEL - AA"
OUT_OF_SCOPE = "SEL - ZZ"


def spec_qs(criteria, **extra):
    q = {"filter_spec": json.dumps({"match": "all", "criteria": criteria})}
    q.update(extra)
    return "&".join(f"{k}={quote(str(v))}" for k, v in q.items())


class _Base(TestCase):
    def _get(self, view, query="", user=None):
        req = self.factory.get(f"/?{query}")
        force_authenticate(req, user=user or self.admin)
        resp = view(req)
        resp.render()
        return resp

    def _ids(self, query="", user=None, view=None):
        resp = self._get(view or DELEGATE_IDS, query, user)
        self.assertEqual(resp.status_code, 200, resp.content)
        return json.loads(resp.content)

    def _walk_list(self, query="", user=None, view=None, page_size=50):
        """
        Every id the LIST endpoint yields, by walking its pages.

        The reference answer. Deliberately assembled the slow way rather than
        read off `count`, because the whole question is which ROWS the two
        endpoints resolve, and two different row sets can share a total.
        """
        view = view or DELEGATE_LIST
        seen, page = [], 1
        while True:
            sep = "&" if query else ""
            resp = self._get(view, f"{query}{sep}page={page}&page_size={page_size}", user)
            self.assertEqual(resp.status_code, 200, resp.content)
            body = json.loads(resp.content)
            seen.extend(row["id"] for row in body["results"])
            if not body.get("next"):
                return seen
            page += 1


class SelectAllReturnsEveryMatchTests(_Base):
    """The bug in one sentence: a page is not the match."""

    @classmethod
    def setUpTestData(cls):
        role = Team.objects.create(name="sel_admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="sel_admin", password="x", role="admin", email="sel@iq-hub.com",
        )
        cls.admin.team = role
        cls.admin.save()

        # 120 rows: more than two default pages, so a page-shaped answer cannot
        # accidentally equal the whole set.
        cls.invoice = BookEvent.objects.create(
            invoice_number="SEL-1", event_code=IN_SCOPE, payment_status="Paid",
        )
        cls.delegates = [
            BookDelegate.objects.create(
                invoice=cls.invoice, event_code=IN_SCOPE,
                first_name="Sel", last_name=f"{i:03d}", email=f"sel{i}@example.com",
            )
            for i in range(120)
        ]

    def setUp(self):
        self.factory = APIRequestFactory()

    def test_it_returns_every_matching_row_not_one_page(self):
        """THE REGRESSION TEST. 120 rows, 50 to a page, 120 ids."""
        body = self._ids()
        self.assertEqual(len(body["ids"]), 120)
        self.assertEqual(body["count"], 120)
        self.assertEqual(set(body["ids"]), {d.id for d in self.delegates})

    def test_the_answer_is_page_size_independent(self):
        """
        The one thing a select-all must never be is a function of how the table
        happens to be paged. page/page_size are meaningless here and are ignored
        rather than honoured — honouring them is the original bug.
        """
        plain = self._ids()["ids"]
        self.assertEqual(self._ids("page=2&page_size=10")["ids"], plain)
        self.assertEqual(self._ids("page_size=500")["ids"], plain)

    def test_it_holds_no_duplicates(self):
        ids = self._ids()["ids"]
        self.assertEqual(len(ids), len(set(ids)),
                         "a duplicate id inflates every count the user is shown")

    def test_it_matches_the_list_endpoint_row_for_row(self):
        self.assertEqual(sorted(self._ids()["ids"]), sorted(self._walk_list()))


class SelectAllAppliesTheSameFiltersAsTheListTests(_Base):
    """
    Each filter surface, asserted against the list rather than a literal.

    A criterion the list applies and this does not hands a mass update rows the
    table was hiding, which is worse than the bug being fixed: the old behaviour
    under-selected visibly, this would over-select invisibly.
    """

    @classmethod
    def setUpTestData(cls):
        role = Team.objects.create(name="sel_f_admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="sel_f_admin", password="x", role="admin", email="self@iq-hub.com",
        )
        cls.admin.team = role
        cls.admin.save()

        cls.inv_paid = BookEvent.objects.create(
            invoice_number="SELF-PAID", event_code=IN_SCOPE,
            payment_status="Paid", ticket_tier="EB",
        )
        cls.inv_pending = BookEvent.objects.create(
            invoice_number="SELF-PEND", event_code=IN_SCOPE,
            payment_status="Pending", ticket_tier="",
        )
        cls.d_paid = BookDelegate.objects.create(
            invoice=cls.inv_paid, event_code=IN_SCOPE,
            first_name="Pay", last_name="Ed", email="paid@example.com",
        )
        cls.d_override = BookDelegate.objects.create(
            invoice=cls.inv_paid, event_code=IN_SCOPE,
            first_name="Ovid", last_name="Override", email="ovid@example.com",
            delegate_payment_status="Cancelled",
        )
        cls.d_pending = BookDelegate.objects.create(
            invoice=cls.inv_pending, event_code=IN_SCOPE,
            first_name="Pen", last_name="Ding", email="pending@example.com",
        )

    def setUp(self):
        self.factory = APIRequestFactory()

    def assertSameAsList(self, query):
        self.assertEqual(
            sorted(self._ids(query)["ids"]), sorted(self._walk_list(query)),
            f"select-all resolved a different row set than the list for: {query}",
        )

    def test_filter_spec_narrows_it(self):
        query = spec_qs([{"field": "payment_status", "op": "is", "value": "Paid"}])
        # Resolved person-level value: the override must be excluded despite its
        # invoice saying Paid. Asserted explicitly as well as against the list,
        # because "both endpoints are wrong the same way" would pass on its own.
        self.assertEqual(set(self._ids(query)["ids"]), {self.d_paid.id})
        self.assertSameAsList(query)

    def test_search_narrows_it(self):
        self.assertSameAsList("search=Override")

    def test_an_unfiltered_call_is_everything(self):
        self.assertEqual(len(self._ids()["ids"]), 3)

    def test_an_invalid_filter_spec_is_a_400_here_too(self):
        """
        Not a detail: a spec this endpoint waved through while the list rejected
        it would select rows against a filter that was never applied.
        """
        resp = self._get(DELEGATE_IDS, spec_qs(
            [{"field": "not_a_field", "op": "is", "value": "x"}]))
        self.assertEqual(resp.status_code, 400, resp.content)


class SelectAllRespectsThePeriodWindowTests(_Base):
    """
    The window PeriodFilterMixin applies to `list` must apply here.

    It did not, and would not have, because period_actions was ("list",) alone.
    A select-all taken while "Last 7 days" was showing 1 row would have resolved
    every row of all time and handed the difference to a mass update — with the
    table still reading "1 matching".
    """

    @classmethod
    def setUpTestData(cls):
        role = Team.objects.create(name="sel_p_admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="sel_p_admin", password="x", role="admin", email="selp@iq-hub.com",
        )
        cls.admin.team = role
        cls.admin.save()

        today = timezone.localdate()
        cls.recent = cls._booking("SELP-NEW", today)
        cls.old = cls._booking("SELP-OLD", today - timedelta(days=200))

    @classmethod
    def _booking(cls, number, when):
        invoice = BookEvent.objects.create(
            invoice_number=number, event_code=IN_SCOPE,
            request_date=when, invoice_date=when, payment_status="Paid",
        )
        return BookDelegate.objects.create(
            invoice=invoice, event_code=IN_SCOPE,
            first_name="Per", last_name=number, email=f"{number}@example.com",
        )

    def setUp(self):
        self.factory = APIRequestFactory()

    def test_the_window_narrows_the_selection(self):
        self.assertEqual(set(self._ids("period=all")["ids"]),
                         {self.recent.id, self.old.id})
        self.assertEqual(set(self._ids("period=last_7_days")["ids"]),
                         {self.recent.id})

    def test_it_agrees_with_the_list_in_every_window(self):
        for period in ("all", "last_7_days", "last_30_days", "last_12_months"):
            with self.subTest(period=period):
                query = f"period={period}"
                self.assertEqual(sorted(self._ids(query)["ids"]),
                                 sorted(self._walk_list(query)))

    def test_an_unknown_period_is_refused_rather_than_treated_as_all(self):
        resp = self._get(DELEGATE_IDS, "period=last_month")
        self.assertEqual(resp.status_code, 400, resp.content)


class SelectAllIsRBACScopedTests(_Base):
    """
    Scoping is inherited from get_queryset(), and this proves it rather than
    trusting it. An unscoped select-all would be the widest read surface in the
    app: one request enumerating every id in a table, for any authenticated user.
    """

    @classmethod
    def setUpTestData(cls):
        all_access = Team.objects.create(name="sel_s_role", is_all_access=True)

        # Passes the all-access gate but is NOT role=admin, so rbac_filter still
        # scopes it. The dangerous combination, same as tests_write_scoping.
        cls.scoped = User.objects.create_user(
            username="sel_scoped", password="x", role="sales", email="sels1@iq-hub.com",
        )
        cls.scoped.team = all_access
        cls.scoped.save()

        cls.admin = User.objects.create_user(
            username="sel_s_admin", password="x", role="admin", email="sels2@iq-hub.com",
        )
        cls.admin.team = all_access
        cls.admin.save()

        cls.event_in = Event.objects.create(
            event_code=IN_SCOPE, name="Scoped", event_date=date(2026, 6, 1))
        Event.objects.create(
            event_code=OUT_OF_SCOPE, name="Hidden", event_date=date(2026, 6, 2))
        cls.scoped.assigned_events.add(cls.event_in)

    def setUp(self):
        self.factory = APIRequestFactory()
        self.inv_in = BookEvent.objects.create(
            invoice_number="SELS-IN", event_code=IN_SCOPE)
        self.inv_out = BookEvent.objects.create(
            invoice_number="SELS-OUT", event_code=OUT_OF_SCOPE)
        self.d_in = BookDelegate.objects.create(
            invoice=self.inv_in, event_code=IN_SCOPE,
            first_name="In", last_name="Scope", email="selin@example.com",
        )
        self.d_out = BookDelegate.objects.create(
            invoice=self.inv_out, event_code=OUT_OF_SCOPE,
            first_name="Out", last_name="Scope", email="selout@example.com",
        )

    def test_a_scoped_caller_gets_only_their_events(self):
        ids = self._ids(user=self.scoped)["ids"]
        self.assertEqual(set(ids), {self.d_in.id})
        self.assertNotIn(
            self.d_out.id, ids,
            "select-all enumerated a row outside the caller's assigned events",
        )

    def test_an_admin_gets_both(self):
        self.assertEqual(set(self._ids(user=self.admin)["ids"]),
                         {self.d_in.id, self.d_out.id})

    def test_the_scoped_caller_agrees_with_their_own_list(self):
        self.assertEqual(sorted(self._ids(user=self.scoped)["ids"]),
                         sorted(self._walk_list(user=self.scoped)))

    def test_it_requires_authentication(self):
        req = self.factory.get("/")
        resp = DELEGATE_IDS(req)
        resp.render()
        self.assertIn(resp.status_code, (401, 403), resp.content)


class SelectAllCeilingTests(_Base):
    """
    Past select_all_max the answer is a refusal, not the first N ids.

    Truncating would be indistinguishable from succeeding at the call site: the
    UI would report "all selected", the remainder would go silently unedited, and
    that is the same class of bug as the one-page selection, only harder to see.
    """

    @classmethod
    def setUpTestData(cls):
        role = Team.objects.create(name="sel_c_admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="sel_c_admin", password="x", role="admin", email="selc@iq-hub.com",
        )
        cls.admin.team = role
        cls.admin.save()
        # Two shapes, so a filter can narrow 5 down to 2.
        cls.tickets = [
            Ticket.objects.create(purpose="Speaker", relationship="direct")
            for _ in range(3)
        ] + [
            Ticket.objects.create(purpose="Speaker", relationship="indirect")
            for _ in range(2)
        ]

    def setUp(self):
        self.factory = APIRequestFactory()

    def test_under_the_ceiling_it_answers_normally(self):
        body = self._ids(view=TICKET_IDS)
        self.assertEqual(len(body["ids"]), 5)
        self.assertEqual(body["max"], TicketViewSet.select_all_max)

    def test_over_the_ceiling_it_refuses_and_names_both_numbers(self):
        original = TicketViewSet.select_all_max
        TicketViewSet.select_all_max = 3
        try:
            resp = self._get(TICKET_IDS)
            self.assertEqual(resp.status_code, 400, resp.content)
            body = json.loads(resp.content)
            self.assertEqual(body["count"], 5)
            self.assertEqual(body["max"], 3)
            self.assertIn("5", body["detail"])
            self.assertIn("3", body["detail"])
            self.assertNotIn("ids", body, "a refusal must not carry a partial set")
        finally:
            TicketViewSet.select_all_max = original

    def test_the_ceiling_counts_the_FILTERED_match_not_the_table(self):
        """
        The count that meets the ceiling is the one AFTER filtering.

        Enforced against the table's size instead, this would refuse every
        ordinary narrow selection on a large table — which is the only way anyone
        uses select-all on Ticket Central, and it would look like the feature
        simply does not work there.
        """
        original = TicketViewSet.select_all_max
        TicketViewSet.select_all_max = 3
        try:
            # Unfiltered is 5, over the ceiling of 3.
            self.assertEqual(self._get(TICKET_IDS).status_code, 400)

            # The same table, narrowed to 2, is answered.
            narrow = spec_qs([
                {"field": "relationship", "op": "is", "value": "indirect"},
            ])
            resp = self._get(TICKET_IDS, narrow)
            self.assertEqual(resp.status_code, 200, resp.content)
            self.assertEqual(len(json.loads(resp.content)["ids"]), 2)
        finally:
            TicketViewSet.select_all_max = original


class SelectAllIsReadOnlyTests(_Base):
    """It is a GET, and it must stay one."""

    @classmethod
    def setUpTestData(cls):
        role = Team.objects.create(name="sel_r_admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="sel_r_admin", password="x", role="admin", email="selr@iq-hub.com",
        )
        cls.admin.team = role
        cls.admin.save()
        cls.invoice = BookEvent.objects.create(
            invoice_number="SELR-1", event_code=IN_SCOPE, payment_status="Paid")
        cls.delegate = BookDelegate.objects.create(
            invoice=cls.invoice, event_code=IN_SCOPE,
            first_name="Read", last_name="Only", email="selr@example.com",
        )

    def setUp(self):
        self.factory = APIRequestFactory()

    def test_it_writes_nothing(self):
        before = (
            BookDelegate.objects.count(),
            BookDelegate.objects.get(pk=self.delegate.pk).updated_at,
        )
        self._ids()
        after = (
            BookDelegate.objects.count(),
            BookDelegate.objects.get(pk=self.delegate.pk).updated_at,
        )
        self.assertEqual(before, after)

    def test_post_is_not_allowed(self):
        req = self.factory.post("/", {}, format="json")
        force_authenticate(req, user=self.admin)
        resp = BookDelegateViewSet.as_view({"get": "ids"})(req)
        resp.render()
        self.assertEqual(resp.status_code, 405, resp.content)
