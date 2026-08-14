"""
events/tests_filter_spec_wiring.py
───────────────────────────────────
Events is the only surface with boolean filter fields, and the only one whose
registry exposes save()-derived columns. Both need their own coverage.

The boolean case matters because a truthy STRING is the exact silent-corruption
shape caught during mass update: assigning "false" to a BooleanField stores
True, because a non-empty string is truthy in Python.
"""
import json
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate


from events.models import Event
from events.views import EventViewSet
from teams.models import Team

User = get_user_model()

LIST = EventViewSet.as_view({"get": "list"})
SCHEMA = EventViewSet.as_view({"get": "filter_schema"})

DERIVED_LABELS = [
    "name", "official_name", "accepting_web_bookings", "city", "country",
    "venue", "tele_marketing_team", "market_research_team", "sales_team",
]


class EventsFilterSpecTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(
            name="ev_filter_admin", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="ev_filter", password="x", role="admin", email="evf@iq-hub.com",
        )
        cls.user.team = cls.role
        cls.user.save()

    def setUp(self):
        self.factory = APIRequestFactory()
        self.on = Event.objects.create(
            event_code="EVF1 - AA", event_date="2026-04-01",
            web_bookings=True, status=Event.Status.LIVE, location="Lisbon",
        )
        self.off = Event.objects.create(
            event_code="EVF2 - BB", event_date="2026-08-01",
            web_bookings=False, status=Event.Status.DRAFT,
        )

    def _ids(self, criteria):
        q = quote(json.dumps({"match": "all", "criteria": criteria}))
        req = self.factory.get(f"/?page=1&page_size=50&filter_spec={q}")
        force_authenticate(req, user=self.user)
        resp = LIST(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        return {r["id"] for r in json.loads(resp.content)["results"]}

    def _schema(self):
        req = self.factory.get("/")
        force_authenticate(req, user=self.user)
        r = SCHEMA(req)
        r.render()
        return json.loads(r.content)

    # ── 2.4 boolean: a REAL JSON boolean, both values ─────────────────────────
    def test_boolean_true_as_real_json_bool(self):
        self.assertEqual(
            self._ids([{"field": "web_bookings", "op": "is", "value": True}]),
            {self.on.id})

    def test_boolean_false_as_real_json_bool(self):
        self.assertEqual(
            self._ids([{"field": "web_bookings", "op": "is", "value": False}]),
            {self.off.id})

    def test_boolean_false_is_not_treated_as_truthy(self):
        """
        The regression guard. If `false` were mishandled as a truthy value, this
        would return the web_bookings=True row instead.
        """
        got = self._ids([{"field": "web_bookings", "op": "is", "value": False}])
        self.assertNotIn(self.on.id, got)

    def test_boolean_string_form_is_also_accepted(self):
        """A <select> yields strings; the backend coerces them to real bools."""
        self.assertEqual(
            self._ids([{"field": "web_bookings", "op": "is", "value": "false"}]),
            {self.off.id})
        self.assertEqual(
            self._ids([{"field": "web_bookings", "op": "is", "value": "true"}]),
            {self.on.id})

    def test_non_nullable_boolean_rejects_is_empty(self):
        q = quote(json.dumps({"match": "all", "criteria": [
            {"field": "web_bookings", "op": "is_empty"}]}))
        req = self.factory.get(f"/?filter_spec={q}")
        force_authenticate(req, user=self.user)
        r = LIST(req)
        r.render()
        self.assertEqual(r.status_code, 400)
        self.assertIn("not valid for a boolean field", json.loads(r.content)["detail"])

    # ── 2.5 derived fields are filterable and labelled ────────────────────────
    def test_derived_fields_carry_the_derived_suffix(self):
        fields = self._schema()["fields"]
        for key in DERIVED_LABELS:
            self.assertIn(key, fields, f"{key} missing from the registry")
            self.assertIn("(derived)", fields[key]["label"],
                          f"{key} label lacks the (derived) marker")

    def test_derived_field_is_filterable_even_though_unwritable(self):
        # accepting_web_bookings is derived from web_bookings in Event.save()
        self.assertEqual(
            self._ids([{"field": "accepting_web_bookings", "op": "is", "value": True}]),
            {self.on.id})

    def test_event_code_is_filterable(self):
        self.assertEqual(
            self._ids([{"field": "event_code", "op": "contains", "value": "EVF1"}]),
            {self.on.id})

    def test_schema_shape(self):
        """
        filter_schema carries type/label/operators/nullable/resolved/
        empty_shape — there is no `group` key; that belongs to the
        bulk_update schema, which is a different endpoint.
        """
        s = self._schema()
        self.assertEqual(s["match_modes"], ["all"])
        wb = s["fields"]["web_bookings"]
        self.assertEqual(wb["type"], "boolean")
        self.assertEqual(wb["operators"], ["is"])       # not nullable -> no is_empty
        self.assertNotIn("group", wb)

    # ── composition with an existing column filter ────────────────────────────
    def test_spec_composes_with_a_column_filter(self):
        """
        Uses city rather than status: EventFilter.filter_status treats 'Live'
        as a DATE predicate (event_date >= today), not the stored status
        field, so a past-dated fixture would never match regardless of the
        spec. Worth knowing, but not what this test is checking.
        """
        q = quote(json.dumps({"match": "all", "criteria": [
            {"field": "web_bookings", "op": "is", "value": True}]}))
        req = self.factory.get(f"/?page=1&page_size=50&city=Lisbon&filter_spec={q}")
        force_authenticate(req, user=self.user)
        r = LIST(req)
        r.render()
        self.assertEqual(r.status_code, 200, r.content)
        # self.on has location=Lisbon (Event.save copies it to city) AND
        # web_bookings=True; self.off satisfies neither.
        self.assertEqual(
            {x["id"] for x in json.loads(r.content)["results"]}, {self.on.id})
