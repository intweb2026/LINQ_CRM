"""
events/tests_bulk_update.py
────────────────────────────
Phase 5: mass update on EventViewSet.

Event.save() (models.py:79-148) derives NINE fields and performs a per-object
SELECT. That makes per-object save() load-bearing here more than on any other
surface — a queryset .update() would desync all nine silently. Several tests
below exist purely to prove save() ran.

NOTE: nothing in this module calls .delete() on a queryset, and the clear_all
endpoint (views.py:555) is never exercised.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import ActionLog
from events.models import Event
from events.views import EventViewSet
from teams.models import Team, TeamPermission

User = get_user_model()

BULK   = EventViewSet.as_view({"post": "bulk_update"})
SCHEMA = EventViewSet.as_view({"get": "bulk_update_schema"})

DERIVED_FIELDS = [
    "name", "official_name", "accepting_web_bookings", "city", "country",
    "venue", "tele_marketing_team", "market_research_team", "sales_team",
]


class EventBulkUpdateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.all_access = Team.objects.create(
            name="ev_bulk_admin", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="ev_bulk", password="x", role="admin", email="ev@iq-hub.com",
        )
        cls.user.team = cls.all_access
        cls.user.save()

        readonly_role, _ = Team.objects.get_or_create(
            name="ev_view_only", defaults={"is_all_access": False},
        )
        TeamPermission.objects.update_or_create(
            team=readonly_role, module="events",
            defaults={"can_view": True, "can_create": False,
                      "can_update": False, "can_delete": False},
        )
        cls.readonly = User.objects.create_user(
            username="ev_readonly", password="x", role="sales", email="evro@iq-hub.com",
        )
        cls.readonly.team = readonly_role
        cls.readonly.save()

    def setUp(self):
        self.factory = APIRequestFactory()
        self.events = [
            Event.objects.create(
                event_code=f"TST{i} - AA", event_date="2026-06-0{}".format(i + 1),
                status=Event.Status.DRAFT, web_bookings=True,
                location="Origin City", official_event_name=f"Original Name {i}",
            )
            for i in range(3)
        ]
        self.ids = [e.id for e in self.events]

    def _post(self, body, user=None):
        req = self.factory.post("/bulk_update/", body, format="json")
        force_authenticate(req, user=user or self.user)
        resp = BULK(req)
        # Calling a view directly returns an unrendered DRF Response; .content
        # raises until it is rendered. Render here so byte-level assertions work.
        resp.render()
        return resp

    def _preview(self, ids, field, value=None, user=None):
        body = {"ids": ids, "field": field, "commit": False}
        if value is not None:
            body["value"] = value
        return self._post(body, user=user)

    def _commit(self, ids, field, value, user=None):
        plan = self._preview(ids, field, value, user=user)
        self.assertEqual(plan.status_code, 200, plan.data)
        return self._post({
            "ids": ids, "field": field, "value": value,
            "commit": True, "plan_hash": plan.data["plan_hash"],
        }, user=user)

    # ── (a) ───────────────────────────────────────────────────────────────────
    def test_a_safe_field_changes_all_and_row_count_holds(self):
        before = Event.objects.count()
        r = self._commit(self.ids, "verdict", Event.Verdict.GOING_AHEAD)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["updated"], 3)
        for e in self.events:
            e.refresh_from_db()
            self.assertEqual(e.verdict, Event.Verdict.GOING_AHEAD)
        self.assertEqual(Event.objects.count(), before)

    # ── (b) per-object save() proof ───────────────────────────────────────────
    def test_b_location_overwrites_city_country_venue(self):
        two = self.ids[:2]
        r = self._commit(two, "location", "Barcelona")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["updated"], 2)
        for e in Event.objects.filter(id__in=two):
            # Only Event.save() does this — a queryset .update() would not.
            self.assertEqual(e.location, "Barcelona")
            self.assertEqual(e.city, "Barcelona")
            self.assertEqual(e.country, "Barcelona")
            self.assertEqual(e.venue, "Barcelona")

    def test_b_location_side_effect_is_declared(self):
        r = self._preview(self.ids, "location", "Anywhere")
        self.assertEqual(r.data["side_effects"],
                         ["also overwrites city, country and venue"])

    # ── (c) ───────────────────────────────────────────────────────────────────
    def test_c_official_event_name_drives_name_and_official_name(self):
        r = self._commit(self.ids, "official_event_name", "Unified Summit 2026")
        self.assertEqual(r.status_code, 200, r.data)
        for e in Event.objects.filter(id__in=self.ids):
            self.assertEqual(e.official_event_name, "Unified Summit 2026")
            self.assertEqual(e.name, "Unified Summit 2026")
            self.assertEqual(e.official_name, "Unified Summit 2026")

    def test_c_official_event_name_side_effect_is_declared(self):
        r = self._preview(self.ids, "official_event_name", "X")
        self.assertEqual(r.data["side_effects"],
                         ["also overwrites name and official_name"])

    # ── (d) ───────────────────────────────────────────────────────────────────
    def test_d_web_bookings_drives_accepting_web_bookings(self):
        r = self._commit(self.ids, "web_bookings", "false")
        self.assertEqual(r.status_code, 200, r.data)
        for e in Event.objects.filter(id__in=self.ids):
            self.assertFalse(e.web_bookings)
            self.assertFalse(e.accepting_web_bookings)   # derived in save()

        back = self._commit(self.ids, "web_bookings", "true")
        self.assertEqual(back.status_code, 200)
        for e in Event.objects.filter(id__in=self.ids):
            self.assertTrue(e.web_bookings)
            self.assertTrue(e.accepting_web_bookings)

    def test_d_web_bookings_off_warns_about_the_webhook(self):
        r = self._preview(self.ids, "web_bookings", "false")
        self.assertEqual(len(r.data["side_effects"]), 1)
        self.assertIn("accepting_web_bookings", r.data["side_effects"][0])
        self.assertIn("webhook", r.data["side_effects"][0])

    def test_d_boolean_string_is_coerced_not_stored_as_text(self):
        """'false' must become the bool False, not a truthy string."""
        self._commit(self.ids[:1], "web_bookings", "false")
        e = Event.objects.get(pk=self.ids[0])
        self.assertIs(e.web_bookings, False)

    # ── (e) exclusions ────────────────────────────────────────────────────────
    def test_e_event_code_rejected(self):
        r = self._preview(self.ids, "event_code", "NEW - XX")
        self.assertEqual(r.status_code, 400)
        self.assertIn("not bulk-editable", r.data["detail"])
        for e in self.events:
            e.refresh_from_db()
            self.assertTrue(e.event_code.startswith("TST"))

    def test_e_edition_rejected(self):
        r = self._preview(self.ids, "edition", 2026)
        self.assertEqual(r.status_code, 400)

    def test_e_all_nine_derived_fields_rejected(self):
        for field in DERIVED_FIELDS:
            r = self._preview(self.ids, field, "anything")
            self.assertEqual(r.status_code, 400, f"{field} was NOT rejected")
            self.assertIn("not bulk-editable", r.data["detail"])

    def test_e_sales_executive_rejected(self):
        r = self._preview(self.ids, "sales_executive", self.user.id)
        self.assertEqual(r.status_code, 400)

    # ── (f) ───────────────────────────────────────────────────────────────────
    def test_f_preview_writes_nothing(self):
        before = Event.objects.count()
        r = self._preview(self.ids, "verdict", Event.Verdict.CANCELLED)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["updated"], 0)
        self.assertEqual(r.data["distribution"], {"": 3})
        for e in self.events:
            e.refresh_from_db()
            self.assertEqual(e.verdict, "")
        self.assertEqual(Event.objects.count(), before)

    # ── (g) ───────────────────────────────────────────────────────────────────
    def test_g_rerun_is_no_op(self):
        self._commit(self.ids, "verdict", Event.Verdict.GOING_AHEAD)
        again = self._preview(self.ids, "verdict", Event.Verdict.GOING_AHEAD)
        self.assertEqual(again.data["no_op"], 3)

    # ── (h) ───────────────────────────────────────────────────────────────────
    def test_h_one_actionlog_with_full_ids(self):
        before = ActionLog.objects.count()
        self._commit(self.ids, "verdict", Event.Verdict.POSTPONED)
        self.assertEqual(ActionLog.objects.count(), before + 1)
        log = ActionLog.objects.latest("created_at")
        self.assertEqual(log.action, "Bulk updated verdict on 3 events")
        self.assertIn(str(sorted(self.ids)), log.details)

    # ── (i) ───────────────────────────────────────────────────────────────────
    def test_i_collateral_empty_and_single_group(self):
        r = self._preview(self.ids, "verdict", Event.Verdict.GOING_AHEAD)
        self.assertEqual(r.data["collateral"]["count"], 0)
        self.assertEqual(r.data["collateral"]["sample"], [])

        req = self.factory.get("/bulk_update_schema/")
        force_authenticate(req, user=self.user)
        s = SCHEMA(req)
        self.assertFalse(s.data["parent_enabled"])
        self.assertEqual({c["group"] for c in s.data["fields"].values()}, {"row"})
        self.assertEqual(s.data["label"], "events")

    # ── (j) ───────────────────────────────────────────────────────────────────
    def test_j_user_without_can_update_is_forbidden(self):
        r = self._preview(self.ids, "verdict", Event.Verdict.GOING_AHEAD, user=self.readonly)
        self.assertEqual(r.status_code, 403)
        for e in self.events:
            e.refresh_from_db()
            self.assertEqual(e.verdict, "")

    # ── Frontend payload contract ─────────────────────────────────────────────
    # These post the EXACT body shape frontend/src/api/events.js builds, rather
    # than a convenient hand-written one. The ViewSet-level tests above all
    # passed while the real browser call was 400ing, because they never
    # exercised the payload the client actually sends.
    #
    #   bulkUpdate: (ids, field, value, commit, planHash) => {
    #     const body = { ids, field, commit, plan_hash: planHash };
    #     if (value !== undefined) body.value = value;
    #     return client.post("/events/bulk_update/", body)...
    #
    # Verified against the real module by loading it in Node with axios stubbed.
    @staticmethod
    def _frontend_body(ids, field, value, commit, plan_hash):
        body = {"ids": ids, "field": field, "commit": commit, "plan_hash": plan_hash}
        if value is not None:            # JS: `if (value !== undefined)`
            body["value"] = value
        return body

    def test_frontend_preview_payload_is_accepted(self):
        body = self._frontend_body(self.ids, "verdict", None, False, None)
        self.assertNotIn("value", body)          # key omitted, not null
        self.assertIsInstance(body["ids"], list)
        r = self._post(body)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["requested"], 3)

    def test_frontend_commit_payload_is_accepted(self):
        """
        Mirrors the modal's real three-call sequence. Picking a field previews
        with NO value; picking a value re-previews WITH it; Apply commits using
        that second hash. Committing against the value-less hash is a 409 by
        design — has_value is part of the digest — so the middle call is not
        optional.
        """
        no_value = self._post(self._frontend_body(self.ids, "verdict", None, False, None))
        self.assertEqual(no_value.status_code, 200)

        with_value = self._post(
            self._frontend_body(self.ids, "verdict", Event.Verdict.GOING_AHEAD, False, None)
        )
        self.assertEqual(with_value.status_code, 200)
        self.assertNotEqual(no_value.data["plan_hash"], with_value.data["plan_hash"])

        r = self._post(self._frontend_body(
            self.ids, "verdict", Event.Verdict.GOING_AHEAD, True, with_value.data["plan_hash"],
        ))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["updated"], 3)

    def test_empty_selection_is_the_30_byte_ids_error(self):
        """
        Regression lock for the reported bug. An empty selection produced
        exactly {"detail":"ids list required"} — 30 bytes — and the three
        indistinguishable causes (empty list, wrong type, missing key) all land
        here, which is why the endpoint now logs what it received.
        """
        for ids in ([], {}, None):
            body = self._frontend_body(ids, "verdict", None, False, None)
            r = self._post(body)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.content, b'{"detail":"ids list required"}')
            self.assertEqual(len(r.content), 30)
        self.assertEqual(Event.objects.count(), 3)

    # ── schema hygiene ────────────────────────────────────────────────────────
    def _schema_fields(self):
        req = self.factory.get("/bulk_update_schema/")
        force_authenticate(req, user=self.user)
        return SCHEMA(req).data["fields"]

    def test_choices_match_model_enum(self):
        self.assertEqual(self._schema_fields()["verdict"]["choices"],
                         list(Event.Verdict.values))

    def test_every_editable_column_is_wired_except_the_documented_exclusions(self):
        """
        The registry is DERIVED from the model, so a column added to Event later
        becomes mass-updatable automatically. That is the intent — but it also
        means a new DERIVED column would be silently offered, so the exclusion
        list is asserted here rather than left to the builder.
        """
        wired = set(self._schema_fields())
        for excluded in [*DERIVED_FIELDS, "event_code", "sales_executive", "status",
                         "id", "created_at", "updated_at", "import_batch_id"]:
            self.assertNotIn(excluded, wired)

        concrete = {
            f.name for f in Event._meta.get_fields()
            if getattr(f, "concrete", False) and getattr(f, "editable", True)
            and not f.primary_key and not f.is_relation
        }
        missing = concrete - wired - set(DERIVED_FIELDS) - {
            # status is retired from every screen; the matrix verdict replaced it
            "event_code", "status", "created_at", "updated_at", "import_batch_id",
        }
        self.assertEqual(missing, set(), f"not offered for mass update: {missing}")

    def test_nullable_mirrors_the_model_column(self):
        """
        `nullable` is what lets the modal offer "clear this field" and what makes
        the backend refuse a null anywhere else, so it must track null=True
        exactly — in both directions.
        """
        columns = {f.name: f for f in Event._meta.get_fields()
                   if getattr(f, "concrete", False)}
        for key, cfg in self._schema_fields().items():
            self.assertEqual(
                bool(cfg.get("nullable", False)), bool(columns[key].null),
                f"{key}: nullable disagrees with the model column",
            )

    def test_end_date_can_be_cleared_but_event_date_cannot(self):
        """The concrete consequence of the rule above."""
        fields = self._schema_fields()
        self.assertTrue(fields["end_date"]["nullable"])
        self.assertFalse(fields["event_date"].get("nullable", False))

        cleared = self._post({
            "ids": self.ids, "field": "event_date", "value": None, "commit": False,
        })
        self.assertEqual(cleared.status_code, 400)
        self.assertIn("cannot be cleared", cleared.data["detail"])

    def test_a_date_column_round_trips(self):
        import datetime
        r = self._commit(self.ids, "website_live_date", "2026-09-01")
        self.assertEqual(r.status_code, 200, r.data)
        for e in Event.objects.filter(id__in=self.ids):
            self.assertEqual(e.website_live_date, datetime.date(2026, 9, 1))

    def test_over_length_text_is_a_400_naming_the_limit_not_a_database_error(self):
        r = self._preview(self.ids, "event_type", "x" * 101)   # max_length=100
        self.assertEqual(r.status_code, 400)
        self.assertIn("100", r.data["detail"])

    def test_telemarketing_team_declares_its_derived_column(self):
        """It writes tele_marketing_team in save(); the preview must say so."""
        r = self._preview(self.ids, "telemarketing_team", "Team A")
        self.assertEqual(r.data["side_effects"], ["also overwrites tele_marketing_team"])
        c = self._commit(self.ids, "telemarketing_team", "Team A")
        self.assertEqual(c.status_code, 200, c.data)
        for e in Event.objects.filter(id__in=self.ids):
            self.assertEqual(e.tele_marketing_team, "Team A")

    def test_market_research_senior_declares_its_derived_column(self):
        r = self._preview(self.ids, "market_research_senior", "Rita")
        self.assertEqual(r.data["side_effects"], ["also overwrites market_research_team"])
        c = self._commit(self.ids, "market_research_senior", "Rita")
        self.assertEqual(c.status_code, 200, c.data)
        for e in Event.objects.filter(id__in=self.ids):
            self.assertEqual(e.market_research_team, "Rita")
