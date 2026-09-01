"""
accounts/tests_filter_spec.py
──────────────────────────────
Filter engine, Phase 1. Exercised against the three real ViewSets rather than a
throwaway one, because the point of this layer is that it composes with each
module's existing FilterSet, ordering and pagination.

Read-only: nothing here writes, and no .delete() is called on any queryset.
"""
import json
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate


from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from events.models import Event
from events.views import EventViewSet
from ticket_central.models import Ticket
from ticket_central.views import TicketViewSet
from teams.models import Team
from webhooks.views import WebhookLogViewSet

User = get_user_model()

DELEGATES = BookDelegateViewSet.as_view({"get": "list"})
DELEG_SCHEMA = BookDelegateViewSet.as_view({"get": "filter_schema"})
TICKETS = TicketViewSet.as_view({"get": "list"})
EVENTS = EventViewSet.as_view({"get": "list"})


def spec_qs(criteria, match="all", **extra):
    q = {"filter_spec": json.dumps({"match": match, "criteria": criteria})}
    q.update(extra)
    return "&".join(f"{k}={quote(str(v))}" for k, v in q.items())


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(
            name="fs_admin", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="fs_user", password="x", role="admin", email="fs@iq-hub.com",
        )
        cls.user.team = cls.role
        cls.user.save()

    def _get(self, view, query="", user=None):
        req = self.factory.get(f"/?{query}")
        force_authenticate(req, user=user or self.user)
        resp = view(req)
        resp.render()
        return resp


class BookingsFilterSpecTests(_Base):
    """Bookings: resolved person-level values, plus the worked example."""

    def setUp(self):
        self.factory = APIRequestFactory()
        # Two invoices, differing payment status; delegates inherit unless overridden.
        self.inv_paid = BookEvent.objects.create(
            invoice_number="FS-PAID", event_code="FS - AA",
            payment_status="Paid", ticket_tier="EB", currency="USD",
        )
        self.inv_pending = BookEvent.objects.create(
            invoice_number="FS-PEND", event_code="FS - AA",
            payment_status="Pending", ticket_tier="", currency="GBP",
        )
        # inherits Paid
        self.d_inherit = BookDelegate.objects.create(
            invoice=self.inv_paid, event_code="FS - AA",
            first_name="Ida", last_name="Inherit", email="ida@example.com",
        )
        # overrides to Cancelled despite a Paid invoice
        self.d_override = BookDelegate.objects.create(
            invoice=self.inv_paid, event_code="FS - AA",
            first_name="Ovid", last_name="Override", email="ovid@example.com",
            delegate_payment_status="Cancelled",
        )
        # inherits Pending, and its invoice has a blank ticket_tier
        self.d_pending = BookDelegate.objects.create(
            invoice=self.inv_pending, event_code="FS - AA",
            first_name="Pat", last_name="Pending", email="pat@example.com",
        )

    def _ids(self, query):
        r = self._get(DELEGATES, query)
        self.assertEqual(r.status_code, 200, r.content)
        return {row["id"] for row in json.loads(r.content)["results"]}

    # ── STEP 5: filtering must match what the table SHOWS ─────────────────────
    def test_resolved_matches_inherited_value_not_the_null_override(self):
        got = self._ids(spec_qs([{"field": "payment_status", "op": "is", "value": "Paid"}]))
        # d_inherit inherits Paid; d_override says Cancelled and must NOT appear.
        self.assertIn(self.d_inherit.id, got)
        self.assertNotIn(self.d_override.id, got)
        self.assertNotIn(self.d_pending.id, got)

    def test_resolved_matches_the_override_over_the_invoice(self):
        got = self._ids(spec_qs([{"field": "payment_status", "op": "is", "value": "Cancelled"}]))
        self.assertEqual(got, {self.d_override.id})

    def test_resolved_none_of_excludes_both_sources(self):
        got = self._ids(spec_qs([
            {"field": "payment_status", "op": "none_of", "values": ["Paid", "Cancelled"]},
        ]))
        self.assertEqual(got, {self.d_pending.id})

    # ── STEP 4: the three is_empty shapes ─────────────────────────────────────
    def test_is_empty_shape_resolved(self):
        """Empty only when the override is unset AND the invoice value is blank."""
        got = self._ids(spec_qs([{"field": "ticket_tier", "op": "is_empty"}]))
        self.assertEqual(got, {self.d_pending.id})      # its invoice tier is ""
        self.assertNotIn(self.d_inherit.id, got)        # inherits "EB"

    def test_is_empty_shape_null_or_blank_on_a_blank_charfield(self):
        got = self._ids(spec_qs([{"field": "sponsorship_level", "op": "is_empty"}]))
        self.assertEqual(len(got), 3)                   # all blank by default
        self.assertEqual(
            self._ids(spec_qs([{"field": "sponsorship_level", "op": "is_not_empty"}])), set()
        )

    def test_is_empty_shape_null_only_on_a_nullable_date(self):
        got = self._ids(spec_qs([{"field": "delegate_payment_date", "op": "is_empty"}]))
        self.assertEqual(len(got), 3)                   # NULL on all three

    def test_is_empty_on_the_raw_override_is_not_the_resolved_shape(self):
        """delegate_payment_status is its own field and keeps null semantics."""
        got = self._ids(spec_qs([{"field": "delegate_payment_status", "op": "is_empty"}]))
        self.assertEqual(got, {self.d_inherit.id, self.d_pending.id})

    # ── The worked example ────────────────────────────────────────────────────
    def test_users_three_criterion_example(self):
        q = spec_qs([
            {"field": "ticket_tier", "op": "is_empty"},
            {"field": "payment_status", "op": "none_of",
             "values": ["Paid", "Cancelled", "Credit Pending (Free)"]},
            {"field": "delegate_count", "op": "contains", "value": "1"},
        ])
        # d_pending: tier empty, status Pending, delegate_count 1 -> matches
        self.assertEqual(self._ids(q), {self.d_pending.id})

    def test_not_contains_over_a_list_means_contains_none_of(self):
        got = self._ids(spec_qs([
            {"field": "last_name", "op": "not_contains", "values": ["Inherit", "Override"]},
        ]))
        self.assertEqual(got, {self.d_pending.id})

    # ── Composition with the existing column FilterSet ────────────────────────
    def test_column_filter_and_spec_intersect(self):
        both = self._ids(
            spec_qs([{"field": "last_name", "op": "contains", "value": "e"}],
                    payment_status="Paid")
        )
        # column filter -> Paid only (d_inherit); spec -> last name contains "e"
        self.assertEqual(both, {self.d_inherit.id})

    def test_spec_does_not_alter_the_filterset_sql(self):
        from book_delegate.filters import BookDelegateFilter
        from django.db.models import Value
        from django.db.models.functions import Coalesce, NullIf

        base = BookDelegate.objects.all()
        only = BookDelegateFilter(data={"payment_status": ["Paid"]}, queryset=base).qs
        composed = only.annotate(
            _fs_ticket_tier=Coalesce(NullIf("delegate_ticket_tier", Value("")),
                                     "invoice__ticket_tier"),
        ).filter(_fs_ticket_tier__isnull=True)

        def predicate(qs):
            return str(qs.query).split(" WHERE ", 1)[1].split(" ORDER BY ")[0]

        # The FilterSet's own predicate survives verbatim inside the composed
        # query — the spec only wraps it and ANDs a clause on.
        self.assertIn(predicate(only), predicate(composed))

    # ── Pagination + ordering still work ──────────────────────────────────────
    def test_pagination_and_ordering_hold_under_a_spec(self):
        q = spec_qs([{"field": "event_code", "op": "is", "value": "FS - AA"}],
                    ordering="id", page_size=2, page=1)
        p1 = self._get(DELEGATES, q)
        self.assertEqual(p1.status_code, 200, p1.content)
        b1 = json.loads(p1.content)
        self.assertEqual(b1["count"], 3)
        self.assertEqual(len(b1["results"]), 2)

        q2 = spec_qs([{"field": "event_code", "op": "is", "value": "FS - AA"}],
                     ordering="id", page_size=2, page=2)
        b2 = json.loads(self._get(DELEGATES, q2).content)
        self.assertEqual(len(b2["results"]), 1)
        ids1 = [r["id"] for r in b1["results"]]
        ids2 = [r["id"] for r in b2["results"]]
        self.assertEqual(ids1, sorted(ids1))                 # ordering respected
        self.assertFalse(set(ids1) & set(ids2))              # page 2 does not repeat page 1

    def test_empty_criteria_equals_no_spec(self):
        with_empty = json.loads(self._get(DELEGATES, spec_qs([])).content)["count"]
        without = json.loads(self._get(DELEGATES, "").content)["count"]
        self.assertEqual(with_empty, without)

    # ── Validation, deny by default ───────────────────────────────────────────
    def _expect_400(self, query, fragment):
        r = self._get(DELEGATES, query)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn(fragment, json.loads(r.content)["detail"])

    def test_unknown_field_rejected(self):
        self._expect_400(spec_qs([{"field": "nope", "op": "is", "value": "x"}]),
                         "is not filterable")

    def test_excluded_fields_rejected(self):
        for f in ("id", "created_at", "updated_at"):
            self._expect_400(spec_qs([{"field": f, "op": "is", "value": "1"}]),
                             "is not filterable")

    def test_bad_operator_for_type_rejected(self):
        # gt is a number/date operator, not a choice one
        self._expect_400(spec_qs([{"field": "payment_status", "op": "gt", "value": "Paid"}]),
                         "is not valid for a choice field")

    def test_between_needs_exactly_two(self):
        self._expect_400(
            spec_qs([{"field": "delegate_count", "op": "between", "values": [1]}]),
            "needs exactly 2 values")

    def test_any_of_needs_at_least_one(self):
        self._expect_400(
            spec_qs([{"field": "payment_status", "op": "any_of", "values": []}]),
            "non-empty 'values' list")

    def test_value_outside_choices_rejected(self):
        self._expect_400(
            spec_qs([{"field": "payment_status", "op": "is", "value": "Banana"}]),
            "is not a valid value")

    def test_over_max_criteria_rejected(self):
        many = [{"field": "first_name", "op": "contains", "value": str(i)} for i in range(21)]
        self._expect_400(spec_qs(many), "Too many criteria")

    def test_match_any_rejected_so_it_can_be_added_later(self):
        self._expect_400(
            spec_qs([{"field": "first_name", "op": "is", "value": "Ida"}], match="any"),
            "Only 'all' is accepted")

    def test_malformed_json_rejected(self):
        self._expect_400("filter_spec=" + quote("{not json"), "not valid JSON")

    # ── RBAC ──────────────────────────────────────────────────────────────────
    def test_spec_cannot_reach_rows_outside_get_queryset(self):
        class _Scoped(BookDelegateViewSet):
            # Narrow the real queryset rather than replacing it — the viewset
            # orders by _sort_request_date, which only the parent annotates.
            def get_queryset(self):
                return super().get_queryset().filter(first_name="Pat")

        view = _Scoped.as_view({"get": "list"})
        req = self.factory.get("/?" + spec_qs(
            [{"field": "last_name", "op": "contains", "value": "e"}]))
        force_authenticate(req, user=self.user)
        r = view(req)
        r.render()
        got = {row["id"] for row in json.loads(r.content)["results"]}
        # d_inherit ("Inherit") also contains "e" but is outside the scope
        self.assertEqual(got, {self.d_pending.id})


class SchemaTests(_Base):
    def setUp(self):
        self.factory = APIRequestFactory()

    def test_schema_shape(self):
        r = self._get(DELEG_SCHEMA)
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.content)
        self.assertEqual(body["match_modes"], ["all"])
        self.assertEqual(body["max_criteria"], 20)
        self.assertIn("operators_by_type", body)
        ps = body["fields"]["payment_status"]
        self.assertTrue(ps["resolved"])
        self.assertEqual(ps["empty_shape"], "resolved")
        self.assertIn("none_of", ps["operators"])

    def test_date_fields_declare_whether_they_carry_a_time(self):
        """
        A DateTimeField is typed "date" so it filters with the date vocabulary,
        but `lte '2026-08-24'` against one is `lte midnight` and drops the whole
        of the day the user asked for. The schema says which columns need the
        end-of-day edge instead of leaving the client to guess from the name.
        """
        r = self._get(DELEG_SCHEMA)
        fields = json.loads(r.content)["fields"]
        # A DateField: the bare date is the whole answer.
        self.assertFalse(fields["request_date"]["has_time"])

        req = self.factory.get("/")
        force_authenticate(req, user=self.user)
        r = WebhookLogViewSet.as_view({"get": "filter_schema"})(req)
        r.render()
        wl = json.loads(r.content)["fields"]
        self.assertEqual(wl["received_at"]["type"], "date")
        self.assertTrue(wl["received_at"]["has_time"])

    def test_user_fk_fields_carry_active_user_choices(self):
        """
        A user FK stores an id but must display a name, so its choices are
        objects {value, label} resolved per request from active users.
        """
        req = self.factory.get("/")
        force_authenticate(req, user=self.user)
        r = TicketViewSet.as_view({"get": "filter_schema"})(req)
        r.render()
        cb = json.loads(r.content)["fields"]["created_by"]
        self.assertEqual(cb["type"], "user_fk")
        self.assertTrue(cb["choices"])
        self.assertEqual(set(cb["choices"][0].keys()), {"value", "label"})
        self.assertIn(self.user.pk, [c["value"] for c in cb["choices"]])

    def test_user_fk_value_is_validated_against_those_choices(self):
        t = Ticket.objects.create(purpose="FKV", type_of_ticket="BX",
                                  created_by=self.user)
        ok = self._get(TICKETS, spec_qs(
            [{"field": "created_by", "op": "is", "value": self.user.pk}]))
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(
            {x["id"] for x in json.loads(ok.content)["results"]}, {t.id})

        bad = self._get(TICKETS, spec_qs(
            [{"field": "created_by", "op": "is", "value": 999999}]))
        self.assertEqual(bad.status_code, 400)
        self.assertIn("is not a valid value", json.loads(bad.content)["detail"])

    def test_company_fk_deliberately_has_no_choices(self):
        """7,671 companies would bloat every schema response; raw id entry instead."""
        r = self._get(DELEG_SCHEMA)
        company = json.loads(r.content)["fields"]["company"]
        self.assertEqual(company["type"], "user_fk")     # coarse FK mapping, flagged
        self.assertNotIn("choices", company)

    def test_non_nullable_boolean_does_not_offer_is_empty(self):
        req = self.factory.get("/")
        force_authenticate(req, user=self.user)
        r = EventViewSet.as_view({"get": "filter_schema"})(req)
        r.render()
        wb = json.loads(r.content)["fields"]["web_bookings"]
        self.assertEqual(wb["type"], "boolean")
        self.assertFalse(wb["nullable"])
        self.assertNotIn("is_empty", wb["operators"])
        # is_not joined the boolean vocabulary so "not ticked" is one criterion
        # the database can answer; without it the table's "Is Not" fell back to
        # filtering the loaded page. is_empty is still absent, which is what this
        # test is about: a NOT NULL column can never be empty, and offering the
        # operator would be offering one that matches nothing.
        self.assertEqual(wb["operators"], ["is", "is_not"])


class TicketAndEventFilterTests(_Base):
    """Per-type operator coverage on the two non-resolved modules."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.t1 = Ticket.objects.create(
            purpose="ALPHA", type_of_ticket="BX", status=Ticket.Status.MR_SUBMITTED,
            priority="AS", estimate=10, organizer="Acme",
        )
        self.t2 = Ticket.objects.create(
            purpose="BETA", type_of_ticket="GR", status=Ticket.Status.COMPLETED,
            priority="DD", estimate=50, organizer="",
        )
        self.e1 = Event.objects.create(
            event_code="FSE1 - AA", event_date="2026-03-01",
            status=Event.Status.LIVE, web_bookings=True, capacity=100,
        )
        self.e2 = Event.objects.create(
            event_code="FSE2 - BB", event_date="2026-09-01",
            status=Event.Status.DRAFT, web_bookings=False, capacity=900,
        )

    def _tids(self, criteria):
        r = self._get(TICKETS, spec_qs(criteria))
        self.assertEqual(r.status_code, 200, r.content)
        return {x["id"] for x in json.loads(r.content)["results"]}

    def _eids(self, criteria):
        r = self._get(EVENTS, spec_qs(criteria))
        self.assertEqual(r.status_code, 200, r.content)
        return {x["id"] for x in json.loads(r.content)["results"]}

    # text
    def test_text_operators(self):
        self.assertEqual(self._tids([{"field": "purpose", "op": "is", "value": "alpha"}]), {self.t1.id})
        self.assertEqual(self._tids([{"field": "purpose", "op": "contains", "value": "LPH"}]), {self.t1.id})
        self.assertEqual(self._tids([{"field": "purpose", "op": "starts_with", "value": "BE"}]), {self.t2.id})
        self.assertEqual(self._tids([{"field": "purpose", "op": "ends_with", "value": "TA"}]), {self.t2.id})
        self.assertEqual(self._tids([{"field": "purpose", "op": "is_not", "value": "ALPHA"}]), {self.t2.id})
        self.assertEqual(
            self._tids([{"field": "purpose", "op": "any_of", "values": ["ALPHA", "BETA"]}]),
            {self.t1.id, self.t2.id})
        self.assertEqual(
            self._tids([{"field": "purpose", "op": "none_of", "values": ["ALPHA"]}]), {self.t2.id})
        self.assertEqual(self._tids([{"field": "organizer", "op": "is_empty"}]), {self.t2.id})
        self.assertEqual(self._tids([{"field": "organizer", "op": "is_not_empty"}]), {self.t1.id})

    # number
    def test_number_operators(self):
        self.assertEqual(self._tids([{"field": "estimate", "op": "gt", "value": 20}]), {self.t2.id})
        self.assertEqual(self._tids([{"field": "estimate", "op": "gte", "value": 50}]), {self.t2.id})
        self.assertEqual(self._tids([{"field": "estimate", "op": "lt", "value": 20}]), {self.t1.id})
        self.assertEqual(self._tids([{"field": "estimate", "op": "lte", "value": 10}]), {self.t1.id})
        self.assertEqual(
            self._tids([{"field": "estimate", "op": "between", "values": [5, 20]}]), {self.t1.id})
        # contains on a number goes through a text cast
        self.assertEqual(self._tids([{"field": "estimate", "op": "contains", "value": "5"}]), {self.t2.id})

    # date
    def test_date_operators(self):
        self.assertEqual(
            self._eids([{"field": "event_date", "op": "before", "value": "2026-06-01"}]), {self.e1.id})
        self.assertEqual(
            self._eids([{"field": "event_date", "op": "after", "value": "2026-06-01"}]), {self.e2.id})
        self.assertEqual(
            self._eids([{"field": "event_date", "op": "between",
                         "values": ["2026-01-01", "2026-06-01"]}]), {self.e1.id})

    def test_not_between_is_the_negation_of_between(self):
        """
        The operator the Advanced Filter's "Is Not / Last 30 Days" is built on.
        It cannot be assembled client-side from two criteria: "outside this
        window" is before OR after, and `match` is "all".
        """
        window = ["2026-01-01", "2026-06-01"]
        self.assertEqual(
            self._eids([{"field": "event_date", "op": "not_between", "values": window}]),
            {self.e2.id})
        # Complementary over these two rows, which is what makes it a usable
        # negation rather than a second, differently-wrong filter.
        self.assertEqual(
            self._eids([{"field": "event_date", "op": "between", "values": window}])
            | self._eids([{"field": "event_date", "op": "not_between", "values": window}]),
            {self.e1.id, self.e2.id})

    def test_not_between_needs_exactly_two_values(self):
        r = self._get(EVENTS, spec_qs(
            [{"field": "event_date", "op": "not_between", "values": ["2026-01-01"]}]))
        self.assertEqual(r.status_code, 400)

    def test_not_between_treats_an_undated_row_exactly_as_is_not_does(self):
        """
        An undated row comes BACK from the negation: Django compiles a negated
        lookup on a nullable column to NOT(... AND col IS NOT NULL), so NULL
        survives it. Pinned rather than merely observed, because the frontend's
        local evaluator is written to match, and the one thing that must not
        drift is the two of them disagreeing about empty cells.
        """
        Event.objects.filter(pk=self.e1.pk).update(website_live_date="2026-02-01")
        Event.objects.filter(pk=self.e2.pk).update(website_live_date="2026-09-01")
        undated = Event.objects.create(
            event_code="FSE3 - CC", event_date="2026-03-02", status=Event.Status.DRAFT)
        self.assertIsNone(undated.website_live_date)
        window = ["2026-01-01", "2026-06-01"]
        got = self._eids([{"field": "website_live_date", "op": "not_between",
                           "values": window}])
        self.assertEqual(got, {self.e2.id, undated.id})
        # The same answer is_not gives on the same undated row.
        self.assertIn(
            undated.id,
            self._eids([{"field": "website_live_date", "op": "is_not", "value": "2026-02-01"}]))

    # boolean + choice
    def test_boolean_and_choice_operators(self):
        self.assertEqual(self._eids([{"field": "web_bookings", "op": "is", "value": True}]), {self.e1.id})
        self.assertEqual(self._eids([{"field": "web_bookings", "op": "is", "value": False}]), {self.e2.id})
        self.assertEqual(
            self._eids([{"field": "status", "op": "any_of", "values": ["Live", "Draft"]}]),
            {self.e1.id, self.e2.id})

    def test_ticket_status_is_filterable_even_though_not_mass_updatable(self):
        """Reading a workflow state cannot route around the submit guards."""
        self.assertEqual(
            self._tids([{"field": "status", "op": "is", "value": "completed"}]), {self.t2.id})

    def test_events_derived_fields_are_filterable(self):
        # accepting_web_bookings is save()-derived; writing it is refused, reading is fine
        self.assertEqual(
            self._eids([{"field": "accepting_web_bookings", "op": "is", "value": True}]), {self.e1.id})
        self.assertEqual(
            self._eids([{"field": "event_code", "op": "contains", "value": "FSE1"}]), {self.e1.id})
