"""
paper_review/tests_scope.py
─────────────────────────────
A2 — row-level scope for paper_review, mirroring
proposal_submission/tests_scope.py field for field. Same regression this design
prevents: RBACMixin.rbac_filter scopes with `event_code__icontains` per assigned
code, so a user assigned "BIU" would also receive every "BIUK - PM" row — a
different event in a different country. paper_review/access.py scopes with exact
set membership (`event_code__in=codes`) instead.

Every out-of-scope detail path must answer 404, never 403: a 403 confirms the row
exists, which is itself a disclosure.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase


from events.models import Event
from paper_review.access import (
    has_full_visibility, may_see_mr_fields, may_use_event_code,
    permitted_event_codes, scope_queryset,
)
from events.testutils import assign_reviewer
from paper_review.models import PaperReview
from teams.models import Team, TeamPermission

U = get_user_model()
LIST = "/api/paper-reviews/"

REQUIRED_EXTRA = {
    "closeness_to_topic": 1, "closeness_to_region": 1,
    "clear_solution_to_challenges": 1, "case_study_results_examples": 1,
    "not_obvious_sales_pitch": 1, "company_profile_score": 1,
    "session_location_on_agenda": "Day 1", "proposal_received": "p",
    "theme": "t", "agenda_addition": "a", "linkedin_speaker": "https://x.com/x",
    "linkedin_followers": 1, "company_name": "Co",
}


def make_event(code, name=None, event_date=date(2026, 5, 1)):
    return Event.objects.create(
        event_code=code, official_event_name=name or f"Event {code}",
        event_date=event_date,
    )


def make_review(code, speaker):
    return PaperReview.objects.create(
        event_code=code, speaker_name=speaker,
        email=f"{speaker.replace(' ', '.').lower()}@example.com",
        paper_submission_date=date(2026, 5, 1),
        internal_footnotes="internal", feedback_to_speaker="fb",
    )


class ScopeBase(APITestCase):
    """Three event codes, deliberately including the BIU / BIUK prefix pair."""

    @classmethod
    def setUpTestData(cls):
        cls.biu  = make_event("BIU",       "Charging USA 2026")
        cls.biuk = make_event("BIUK - PM", "EV Charging UK 2026")
        cls.afs  = make_event("AFS - JS",  "Aviation Fuel Summit 2026")

        cls.r_biu  = make_review("BIU",       "Biu Speaker")
        cls.r_biuk = make_review("BIUK - PM", "Biuk Speaker")
        cls.r_afs  = make_review("AFS - JS",  "Afs Speaker")

        # A role granting the module outright — scope must come from
        # assignments, not from the module grant.
        cls.role = Team.objects.create(name="Reviews Full")
        TeamPermission.objects.create(
            team=cls.role, module="paper_review",
            can_view=True, can_create=True, can_update=True, can_delete=True,
        )
        cls.all_access_role = Team.objects.create(
            name="All Access PR", is_all_access=True)

        cls.admin = U.objects.create_user(
            username="pr_scope_admin", password="x", email="a@x.com",
            role="admin", team=cls.role)
        cls.hp = U.objects.create_user(
            username="HP", password="x", email="hp@x.com",
            role="sales", team=cls.role)
        cls.all_access = U.objects.create_user(
            username="pr_scope_allaccess", password="x", email="aa@x.com",
            role="sales", team=cls.all_access_role)
        # Market research, assigned to exactly ONE event (BIU).
        cls.mr_biu = U.objects.create_user(
            username="pr_scope_mr_biu", password="x", email="mr@x.com",
            role="market_research", team=cls.role)
        assign_reviewer(cls.mr_biu, cls.biu)
        # Assigned to nothing at all.
        cls.unassigned = U.objects.create_user(
            username="pr_scope_none", password="x", email="n@x.com",
            role="sales", team=cls.role)

    def codes_seen_by(self, user):
        self.client.force_authenticate(user=user)
        r = self.client.get(LIST, {"page_size": 50})
        self.assertEqual(r.status_code, 200, r.content)
        return sorted(row["event_code"] for row in r.data["results"]), r.data


class PredicateTests(TestCase):
    """The bypass predicate, in isolation — one definition, three bypasses."""

    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(name="Plain PR")
        cls.all_access_role = Team.objects.create(
            name="AA PR", is_all_access=True)
        cls.admin = U.objects.create_user(
            username="pr_p_admin", password="x", email="1@x.com", role="admin")
        cls.hp = U.objects.create_user(
            username="HP", password="x", email="2@x.com", role="sales")
        cls.aa = U.objects.create_user(
            username="pr_p_aa", password="x", email="3@x.com", role="sales",
            team=cls.all_access_role)
        cls.plain = U.objects.create_user(
            username="pr_p_plain", password="x", email="4@x.com", role="sales",
            team=cls.role)
        cls.mr = U.objects.create_user(
            username="pr_p_mr", password="x", email="5@x.com",
            role="market_research", team=cls.role)

    def test_full_visibility_for_the_three_bypasses_only(self):
        for user in (self.admin, self.hp, self.aa):
            with self.subTest(user=user.username):
                self.assertTrue(has_full_visibility(user))
        for user in (self.plain, self.mr):
            with self.subTest(user=user.username):
                self.assertFalse(has_full_visibility(user))

    def test_anonymous_has_no_visibility(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(has_full_visibility(AnonymousUser()))
        self.assertFalse(has_full_visibility(None))

    def test_mr_field_rule_is_full_visibility_plus_market_research(self):
        for user in (self.admin, self.hp, self.aa, self.mr):
            with self.subTest(user=user.username):
                self.assertTrue(may_see_mr_fields(user))
        self.assertFalse(may_see_mr_fields(self.plain))

    def test_permitted_codes_come_only_from_the_assignment_m2m(self):
        ev = make_event("SOLO - PR")
        self.assertEqual(permitted_event_codes(self.plain), [])
        assign_reviewer(self.plain, ev)
        self.assertEqual(permitted_event_codes(self.plain), ["SOLO - PR"])

    def test_scope_queryset_never_degenerates_to_everything(self):
        make_review("SOLO - PR", "Someone")
        make_event("SOLO - PR")
        qs = PaperReview.objects.all()
        self.assertEqual(scope_queryset(qs, self.plain).count(), 0)
        self.assertEqual(scope_queryset(qs, self.admin).count(), qs.count())

    def test_may_use_event_code(self):
        ev = make_event("USE - PR")
        self.assertTrue(may_use_event_code(self.admin, "ANYTHING"))
        self.assertFalse(may_use_event_code(self.plain, "USE - PR"))
        assign_reviewer(self.plain, ev)
        self.assertTrue(may_use_event_code(self.plain, "USE - PR"))


class ScopeQuerySetSQLTests(TestCase):
    """
    [OBSERVED] scope_queryset compiles to an IN clause on event_code, never a
    LIKE/icontains loop — the specific defect access.py's own docstring names as
    the reason RBACMixin.rbac_filter was not reused (`event_code__icontains` per
    assigned code over-grants "BIU" onto "BIUK - PM"). Asserted directly against
    the compiled SQL rather than only against behaviour, so a future edit to
    scope_queryset that reintroduces icontains fails here even if some scoping
    behaviour happens to still look right by accident.
    """

    def test_scoped_query_compiles_to_in_not_like(self):
        role = Team.objects.create(name="SQL Role")
        user = U.objects.create_user(
            username="pr_sql_user", password="x", email="sql@x.com",
            role="sales", team=role)
        biu = make_event("BIU")
        make_event("BIUK - PM")
        assign_reviewer(user, biu)

        qs = scope_queryset(PaperReview.objects.all(), user)
        sql = str(qs.query).upper()
        self.assertIn(" IN (", sql)
        self.assertNotIn("LIKE", sql)

    def test_full_visibility_emits_no_where_clause_at_all(self):
        """
        event_code legitimately appears in the SELECT column list regardless —
        the claim under test is that full visibility adds no FILTER, so the
        query has no WHERE clause at all.
        """
        admin = U.objects.create_user(
            username="pr_sql_admin", password="x", email="sqladmin@x.com",
            role="admin")
        qs = scope_queryset(PaperReview.objects.all(), admin)
        self.assertNotIn("WHERE", str(qs.query).upper())


class ListScopeTests(ScopeBase):
    def test_admin_sees_everything(self):
        codes, _ = self.codes_seen_by(self.admin)
        self.assertEqual(codes, ["AFS - JS", "BIU", "BIUK - PM"])

    def test_dapi_sees_everything(self):
        codes, _ = self.codes_seen_by(self.hp)
        self.assertEqual(codes, ["AFS - JS", "BIU", "BIUK - PM"])

    def test_all_access_non_admin_sees_everything(self):
        codes, _ = self.codes_seen_by(self.all_access)
        self.assertEqual(codes, ["AFS - JS", "BIU", "BIUK - PM"])

    def test_assigned_biu_sees_no_biuk_rows(self):
        """
        THE regression this design prevents. An icontains scope would return
        BIUK - PM here because "BIUK - PM" contains "BIU".
        """
        codes, _ = self.codes_seen_by(self.mr_biu)
        self.assertEqual(codes, ["BIU"])
        self.assertNotIn("BIUK - PM", codes)
        self.assertNotIn("AFS - JS", codes)

    def test_unassigned_user_sees_nothing(self):
        """Must be .none(), NOT an unfiltered queryset."""
        codes, body = self.codes_seen_by(self.unassigned)
        self.assertEqual(codes, [])
        self.assertEqual(body["count"], 0)

    def test_counts_reflect_the_scoped_queryset(self):
        """A leaked total discloses volume outside scope."""
        _, admin_body = self.codes_seen_by(self.admin)
        self.assertEqual(admin_body["count"], 3)

        _, biu_body = self.codes_seen_by(self.mr_biu)
        self.assertEqual(biu_body["count"], 1)
        self.assertEqual(biu_body["total_pages"], 1)
        self.assertEqual(len(biu_body["results"]), 1)

        _, none_body = self.codes_seen_by(self.unassigned)
        self.assertEqual(none_body["count"], 0)
        self.assertEqual(len(none_body["results"]), 0)

    def test_count_with_a_small_page_size_still_scoped(self):
        for i in range(4):
            make_review("BIU", f"Extra {i}")
        self.client.force_authenticate(user=self.mr_biu)
        r = self.client.get(LIST, {"page_size": 2})
        self.assertEqual(r.data["count"], 5)          # 1 original + 4, BIU only
        self.assertEqual(r.data["total_pages"], 3)
        self.assertEqual(len(r.data["results"]), 2)

    def test_a_client_supplied_search_cannot_widen_scope(self):
        self.client.force_authenticate(user=self.mr_biu)
        r = self.client.get(LIST, {"search": "Afs Speaker"})
        self.assertEqual(r.data["count"], 0)

    def test_filter_spec_cannot_widen_scope(self):
        import json
        from urllib.parse import quote
        spec = {"match": "all", "criteria": [
            {"field": "event_code", "op": "is", "value": "AFS - JS"}]}
        self.client.force_authenticate(user=self.mr_biu)
        r = self.client.get(f"{LIST}?filter_spec={quote(json.dumps(spec))}")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["count"], 0)


class DetailScopeTests(ScopeBase):
    """Out-of-scope detail access is 404, never 403 — GET / PATCH / PUT / DELETE."""

    def _full_put_payload(self, code):
        return {
            "event_code": code, "speaker_name": "X", "email": "x@x.com",
            **REQUIRED_EXTRA,
        }

    def test_every_detail_verb_is_404_out_of_scope(self):
        self.client.force_authenticate(user=self.mr_biu)
        url = f"{LIST}{self.r_afs.id}/"
        cases = [
            ("GET",    lambda: self.client.get(url)),
            ("PATCH",  lambda: self.client.patch(url, {"company_name": "X"},
                                                 format="json")),
            ("PUT",    lambda: self.client.put(
                url, self._full_put_payload("BIU"), format="json")),
            ("DELETE", lambda: self.client.delete(url)),
        ]
        for verb, call in cases:
            with self.subTest(verb=verb):
                r = call()
                self.assertEqual(
                    r.status_code, 404,
                    f"{verb} returned {r.status_code}; 403 would confirm the row exists")

    def test_unassigned_user_gets_404_on_every_row(self):
        self.client.force_authenticate(user=self.unassigned)
        for r_ in (self.r_biu, self.r_biuk, self.r_afs):
            with self.subTest(row=r_.id):
                self.assertEqual(self.client.get(f"{LIST}{r_.id}/").status_code, 404)

    def test_in_scope_detail_is_200(self):
        self.client.force_authenticate(user=self.mr_biu)
        r = self.client.get(f"{LIST}{self.r_biu.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["event_code"], "BIU")

    def test_admin_reaches_every_row(self):
        self.client.force_authenticate(user=self.admin)
        for r_ in (self.r_biu, self.r_biuk, self.r_afs):
            with self.subTest(row=r_.id):
                self.assertEqual(self.client.get(f"{LIST}{r_.id}/").status_code, 200)


class CreateScopeTests(ScopeBase):
    """Creation is scoped: an unscoped create would vanish from its author's list."""

    def payload(self, code, **over):
        base = {
            "event_code": code, "speaker_name": "New Person",
            "email": "new.person@example.com",
            "paper_submission_date": "2026-08-10",
            **REQUIRED_EXTRA,
        }
        base.update(over)
        return base

    def test_create_out_of_scope_is_400_on_event_code(self):
        self.client.force_authenticate(user=self.mr_biu)
        r = self.client.post(LIST, self.payload("AFS - JS"), format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("event_code", r.data)
        self.assertIn("not assigned", str(r.data["event_code"]).lower())

    def test_create_in_scope_is_201(self):
        self.client.force_authenticate(user=self.mr_biu)
        r = self.client.post(LIST, self.payload("BIU"), format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["event_code"], "BIU")

    def test_created_row_is_visible_to_its_author(self):
        self.client.force_authenticate(user=self.mr_biu)
        created = self.client.post(LIST, self.payload("BIU"), format="json")
        self.assertEqual(created.status_code, 201, created.content)
        listed = self.client.get(LIST, {"page_size": 50})
        self.assertIn(created.data["id"],
                      [row["id"] for row in listed.data["results"]])

    def test_prefix_code_cannot_be_smuggled_in(self):
        """mr_biu is assigned BIU. "BIUK - PM" must not pass because BIU is a prefix."""
        self.client.force_authenticate(user=self.mr_biu)
        r = self.client.post(LIST, self.payload("BIUK - PM"), format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("event_code", r.data)

    def test_unassigned_user_cannot_create_anything(self):
        self.client.force_authenticate(user=self.unassigned)
        for code in ("BIU", "BIUK - PM", "AFS - JS"):
            with self.subTest(code=code):
                r = self.client.post(LIST, self.payload(code, email=f"u{code}@x.com"
                                                        .replace(" ", "")),
                                     format="json")
                self.assertEqual(r.status_code, 400, r.content)

    def test_admin_can_create_for_any_event(self):
        self.client.force_authenticate(user=self.admin)
        for code in ("BIU", "BIUK - PM", "AFS - JS"):
            with self.subTest(code=code):
                body = self.payload(code)
                body["email"] = f"a{code.replace(' ', '')}@x.com"
                r = self.client.post(LIST, body, format="json")
                self.assertEqual(r.status_code, 201, r.content)

    def test_patch_cannot_move_a_row_out_of_scope(self):
        self.client.force_authenticate(user=self.mr_biu)
        r = self.client.patch(f"{LIST}{self.r_biu.id}/",
                              {"event_code": "AFS - JS"}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("event_code", r.data)
        self.r_biu.refresh_from_db()
        self.assertEqual(self.r_biu.event_code, "BIU")
