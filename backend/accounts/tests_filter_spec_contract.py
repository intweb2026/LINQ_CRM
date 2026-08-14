"""
accounts/tests_filter_spec_contract.py
───────────────────────────────────────
Frontend→backend wire contract for the filter engine.

The mass-update Events bug shipped because every test built its own convenient
payload and none exercised what the frontend actually sends. These strings are
NOT hand-written: each is the literal output of `encodeSpec(buildCriterion(...))`
from frontend/src/hooks/useFilterSpec.js, captured by running the real module in
Node with its React import stripped.

If one of these 400s, the frontend serializer is wrong — fix the serializer,
not the literal.
"""
import json
from urllib.parse import unquote

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

User = get_user_model()

DELEGATES = BookDelegateViewSet.as_view({"get": "list"})
EVENTS = EventViewSet.as_view({"get": "list"})
TICKETS = TicketViewSet.as_view({"get": "list"})

# ── Literals captured from the real hook ─────────────────────────────────────
WIRE = {
    "is_empty":
        "%7B%22match%22%3A%22all%22%2C%22criteria%22%3A%5B%7B%22field%22%3A%22ticket_tier%22%2C%22op%22%3A%22is_empty%22%7D%5D%7D",
    "none_of":
        "%7B%22match%22%3A%22all%22%2C%22criteria%22%3A%5B%7B%22field%22%3A%22payment_status%22%2C%22op%22%3A%22none_of%22%2C%22values%22%3A%5B%22Paid%22%2C%22Cancelled%22%2C%22Credit%20Pending%20(Free)%22%5D%7D%5D%7D",
    "contains":
        "%7B%22match%22%3A%22all%22%2C%22criteria%22%3A%5B%7B%22field%22%3A%22delegate_count%22%2C%22op%22%3A%22contains%22%2C%22value%22%3A%221%22%7D%5D%7D",
    "between":
        "%7B%22match%22%3A%22all%22%2C%22criteria%22%3A%5B%7B%22field%22%3A%22delegate_count%22%2C%22op%22%3A%22between%22%2C%22values%22%3A%5B0%2C5%5D%7D%5D%7D",
    "boolean_is":
        "%7B%22match%22%3A%22all%22%2C%22criteria%22%3A%5B%7B%22field%22%3A%22web_bookings%22%2C%22op%22%3A%22is%22%2C%22value%22%3Atrue%7D%5D%7D",
    "user_fk_is":
        "%7B%22match%22%3A%22all%22%2C%22criteria%22%3A%5B%7B%22field%22%3A%22created_by%22%2C%22op%22%3A%22is%22%2C%22value%22%3A109%7D%5D%7D",
}


class WireContractTests(TestCase):
    """
    Each literal goes to the module that owns its field — web_bookings is an
    Events field and created_by a Ticket field, so sending either to
    /api/delegates/ would (correctly) 400 as not filterable.
    """

    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(
            name="wire_admin", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="wire_user", password="x", role="admin", email="wire@iq-hub.com",
        )
        cls.user.team = cls.role
        cls.user.save()

    def setUp(self):
        self.factory = APIRequestFactory()

        self.inv_paid = BookEvent.objects.create(
            invoice_number="WC-PAID", event_code="WC - AA",
            payment_status="Paid", ticket_tier="EB",
        )
        self.inv_pending = BookEvent.objects.create(
            invoice_number="WC-PEND", event_code="WC - AA",
            payment_status="Pending", ticket_tier="",
        )
        self.d_paid = BookDelegate.objects.create(
            invoice=self.inv_paid, event_code="WC - AA",
            first_name="Pa", last_name="Id", email="pa@example.com",
        )
        self.d_pending = BookDelegate.objects.create(
            invoice=self.inv_pending, event_code="WC - AA",
            first_name="Pe", last_name="Nd", email="pe@example.com",
        )

        self.ev_on = Event.objects.create(
            event_code="WC1 - AA", event_date="2026-05-01", web_bookings=True)
        self.ev_off = Event.objects.create(
            event_code="WC2 - BB", event_date="2026-05-02", web_bookings=False)

        self.t_mine = Ticket.objects.create(
            purpose="WCT", type_of_ticket="BX", created_by=self.user)
        self.t_other = Ticket.objects.create(purpose="WCO", type_of_ticket="BX")

    def _get(self, view, encoded_spec):
        req = self.factory.get(
            f"/?page=1&page_size=50&filter_spec={encoded_spec}")
        force_authenticate(req, user=self.user)
        resp = view(req)
        resp.render()
        return resp

    def _ids(self, view, encoded_spec):
        r = self._get(view, encoded_spec)
        self.assertEqual(r.status_code, 200, r.content)
        return {row["id"] for row in json.loads(r.content)["results"]}

    # ── One test per wire shape ───────────────────────────────────────────────
    def test_shape_is_empty_no_value_key(self):
        """is_empty must carry neither 'value' nor 'values'."""
        payload = json.loads(unquote(WIRE["is_empty"]))
        crit = payload["criteria"][0]
        self.assertEqual(set(crit.keys()), {"field", "op"})
        # inv_pending has a blank tier and the delegate inherits it
        self.assertEqual(self._ids(DELEGATES, WIRE["is_empty"]), {self.d_pending.id})

    def test_shape_none_of_values_list(self):
        crit = json.loads(unquote(WIRE["none_of"]))["criteria"][0]
        self.assertEqual(set(crit.keys()), {"field", "op", "values"})
        self.assertEqual(len(crit["values"]), 3)
        # excludes the Paid delegate, keeps the Pending one
        self.assertEqual(self._ids(DELEGATES, WIRE["none_of"]), {self.d_pending.id})

    def test_shape_contains_single_value_on_a_number(self):
        crit = json.loads(unquote(WIRE["contains"]))["criteria"][0]
        self.assertEqual(set(crit.keys()), {"field", "op", "value"})
        # delegate_count defaults to 1 on both, cast to text and matched
        self.assertEqual(
            self._ids(DELEGATES, WIRE["contains"]),
            {self.d_paid.id, self.d_pending.id},
        )

    def test_shape_between_exactly_two_values(self):
        crit = json.loads(unquote(WIRE["between"]))["criteria"][0]
        self.assertEqual(set(crit.keys()), {"field", "op", "values"})
        self.assertEqual(len(crit["values"]), 2)
        self.assertEqual(
            self._ids(DELEGATES, WIRE["between"]),
            {self.d_paid.id, self.d_pending.id},
        )

    def test_shape_boolean_is_true(self):
        crit = json.loads(unquote(WIRE["boolean_is"]))["criteria"][0]
        self.assertIs(crit["value"], True)          # a real JSON bool, not "true"
        self.assertEqual(self._ids(EVENTS, WIRE["boolean_is"]), {self.ev_on.id})

    def test_shape_user_fk_is(self):
        """
        The captured literal carries id 109 — HP's pk in the dev database, which
        does not exist here. The SHAPE is asserted against the literal; the
        semantic check re-encodes with this test's user id, because a user FK is
        validated against the live active-user list.
        """
        crit = json.loads(unquote(WIRE["user_fk_is"]))["criteria"][0]
        self.assertEqual(set(crit.keys()), {"field", "op", "value"})
        self.assertIsInstance(crit["value"], int)

        stale = self._get(TICKETS, WIRE["user_fk_is"])
        self.assertEqual(stale.status_code, 400)     # id 109 is not an active user here
        self.assertIn("is not a valid value", json.loads(stale.content)["detail"])

        from urllib.parse import quote
        live = quote(json.dumps({"match": "all", "criteria": [
            {**crit, "value": self.user.pk}]}))
        self.assertEqual(self._ids(TICKETS, live), {self.t_mine.id})

    # ── The whole three-criterion example, as the UI would send it ────────────
    def test_full_example_round_trip(self):
        from urllib.parse import quote
        criteria = [
            json.loads(unquote(WIRE["is_empty"]))["criteria"][0],
            json.loads(unquote(WIRE["none_of"]))["criteria"][0],
            json.loads(unquote(WIRE["contains"]))["criteria"][0],
        ]
        encoded = quote(json.dumps({"match": "all", "criteria": criteria}))
        got = self._ids(DELEGATES, encoded)
        # tier empty AND status not in the three AND count contains "1"
        self.assertEqual(got, {self.d_pending.id})

    # ── Phase 3: the full param string a wired table sends ────────────────────
    # Literal output of the shared serializeParams (api/client.js) given the
    # params object BookingsTable.load() assembles with a spec, a column filter,
    # ordering and a page all active. Captured in Node from the real serializer.
    COMPOSED_QUERY = (
        "page=1&page_size=50&ordering=-_sort_request_date&event_code=WC+-+AA"
        "&filter_spec=%7B%22match%22%3A%22all%22%2C%22criteria%22%3A%5B"
        "%7B%22field%22%3A%22ticket_tier%22%2C%22op%22%3A%22is_empty%22%7D%2C"
        "%7B%22field%22%3A%22payment_status%22%2C%22op%22%3A%22none_of%22%2C"
        "%22values%22%3A%5B%22Paid%22%2C%22Cancelled%22%5D%7D%5D%7D"
    )

    def test_composed_query_is_not_double_encoded(self):
        """
        Regression for the bug the first real integration exposed: the hook used
        to pre-percent-encode, then URLSearchParams encoded again, so Django saw
        the literal text '%7B%22match%22…' and answered 'not valid JSON'.
        """
        req = self.factory.get(f"/?{self.COMPOSED_QUERY}")
        force_authenticate(req, user=self.user)
        r = DELEGATES(req)
        r.render()
        self.assertEqual(r.status_code, 200, r.content)

    def test_column_filter_and_spec_compose_as_intersection(self):
        def ids(query):
            req = self.factory.get(f"/?{query}")
            force_authenticate(req, user=self.user)
            resp = DELEGATES(req)
            resp.render()
            self.assertEqual(resp.status_code, 200, resp.content)
            return {x["id"] for x in json.loads(resp.content)["results"]}

        spec_only = (
            "page=1&page_size=50&filter_spec="
            + self.COMPOSED_QUERY.split("&filter_spec=")[1]
        )
        column_only = "page=1&page_size=50&event_code=WC+-+AA"

        s = ids(spec_only)
        c = ids(column_only)
        both = ids(self.COMPOSED_QUERY)

        # spec alone: blank tier AND status not Paid/Cancelled -> the pending one
        self.assertEqual(s, {self.d_pending.id})
        # column alone: both delegates share event_code WC - AA
        self.assertEqual(c, {self.d_paid.id, self.d_pending.id})
        # together: the intersection, not one overriding the other
        self.assertEqual(both, s & c)
        self.assertEqual(both, {self.d_pending.id})

    def test_no_spec_request_carries_no_filter_spec_key(self):
        """Byte-identical to pre-phase behaviour: the key must be absent."""
        query = "page=1&page_size=50&ordering=-_sort_request_date"
        self.assertNotIn("filter_spec", query)
        req = self.factory.get(f"/?{query}")
        force_authenticate(req, user=self.user)
        r = DELEGATES(req)
        r.render()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(r.content)["count"], 2)   # unfiltered

    def test_empty_filter_spec_value_is_ignored_not_an_error(self):
        """`filter_spec=` (empty) must behave as no spec, never as a parse error."""
        req = self.factory.get("/?page=1&page_size=50&filter_spec=")
        force_authenticate(req, user=self.user)
        r = DELEGATES(req)
        r.render()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(json.loads(r.content)["count"], 2)

    def test_parentheses_survive_encodeuricomponent(self):
        """
        encodeURIComponent leaves ( and ) unescaped. 'Credit Pending (Free)' must
        still round-trip through the query string and match the choices list.
        """
        raw = unquote(WIRE["none_of"])
        self.assertIn("Credit Pending (Free)", json.loads(raw)["criteria"][0]["values"])
        self.assertEqual(self._get(DELEGATES, WIRE["none_of"]).status_code, 200)
