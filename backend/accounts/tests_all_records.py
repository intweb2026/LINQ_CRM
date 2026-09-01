"""
accounts/tests_all_records.py
──────────────────────────────
The per-module row-scope cell — can_all, the "All records" column of the
permission grid.

The question it answers is the one that had no answer before it: give ONE person
every paper review without giving them every booking and every event too. The
three bypasses that already existed — the admin role, the HP account, an
is_all_access team — are all-or-nothing across the whole app, so the only way to
share a single module was to share all of them.

What is asserted here, in order:

  1. it resolves, from a team's row and from a person's own override, and the
     override's three states behave like the other four cells: None inherits,
     False revokes what the team granted;
  2. it actually widens the queryset for paper review, the module the feature
     was asked for;
  3. it does NOT widen anything else. This is the test that matters. A grant
     that leaked across modules would be indistinguishable from is_all_access
     while looking narrow in the grid, which is worse than not having it at all;
  4. it survives the PUT the permission modal sends, rather than being accepted
     and quietly dropped.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient, APITestCase

from accounts.crm_permissions import has_module_action
from accounts.models import UserPermission
from events.models import Event
from paper_review.access import has_full_visibility, scope_queryset
from paper_review.models import PaperReview
from teams.models import Team, TeamPermission

U = get_user_model()
PR_LIST = "/api/paper-reviews/"
EV_LIST = "/api/events/"


def make_event(code):
    return Event.objects.create(
        event_code=code, official_event_name=f"Event {code}",
        event_date=date(2026, 5, 1),
    )


def make_review(code, speaker):
    return PaperReview.objects.create(
        event_code=code, speaker_name=speaker,
        email=f"{speaker.replace(' ', '.').lower()}@example.com",
        paper_submission_date=date(2026, 5, 1),
    )


class Base(APITestCase):
    """
    One team that can OPEN paper review and events but is scoped to its own rows,
    and three events carrying one review each. Nobody here is assigned an event,
    so every widening seen below comes from can_all and from nothing else.
    """

    @classmethod
    def setUpTestData(cls):
        cls.biu = make_event("BIU")
        cls.afs = make_event("AFS - JS")
        cls.pmx = make_event("PMX - EU")
        for code, who in (("BIU", "Biu One"), ("AFS - JS", "Afs Two"),
                          ("PMX - EU", "Pmx Three")):
            make_review(code, who)

        cls.team = Team.objects.create(name="Reviewers, scoped")
        for module in ("paper_review", "events"):
            TeamPermission.objects.create(
                team=cls.team, module=module,
                can_view=True, can_create=True, can_update=True,
                can_delete=True, can_all=False,
            )

        cls.plain = U.objects.create_user(
            username="ar_plain", password="x", email="p@x.com",
            role="sales", team=cls.team)
        cls.shared = U.objects.create_user(
            username="ar_shared", password="x", email="s@x.com",
            role="sales", team=cls.team)
        UserPermission.objects.create(
            user=cls.shared, module="paper_review", can_all=True)

    def codes_seen(self, user):
        self.client.force_authenticate(user=user)
        r = self.client.get(PR_LIST, {"page_size": 50})
        self.assertEqual(r.status_code, 200, r.content)
        return sorted(row["event_code"] for row in r.data["results"])


class ResolutionTests(Base):

    def test_the_cell_reaches_effective_permissions(self):
        self.assertFalse(self.plain.effective_permissions()["paper_review"]["all"])
        self.assertTrue(self.shared.effective_permissions()["paper_review"]["all"])

    def test_it_inherits_from_the_team_like_every_other_cell(self):
        TeamPermission.objects.filter(
            team=self.team, module="paper_review").update(can_all=True)
        fresh = U.objects.get(pk=self.plain.pk)
        self.assertTrue(fresh.effective_permissions()["paper_review"]["all"])

    def test_false_revokes_what_the_team_granted(self):
        TeamPermission.objects.filter(
            team=self.team, module="paper_review").update(can_all=True)
        UserPermission.objects.create(
            user=self.plain, module="paper_review", can_all=False)
        fresh = U.objects.get(pk=self.plain.pk)
        self.assertFalse(fresh.effective_permissions()["paper_review"]["all"])

    def test_view_is_still_a_prerequisite(self):
        """All-records on a module somebody cannot open grants nothing."""
        TeamPermission.objects.filter(
            team=self.team, module="paper_review").update(can_view=False)
        fresh = U.objects.get(pk=self.shared.pk)
        self.assertFalse(has_module_action(fresh, "paper_review", "all"))
        self.assertFalse(has_full_visibility(fresh))


class PaperReviewScopeTests(Base):

    def test_unshared_user_assigned_nothing_sees_nothing(self):
        self.assertEqual(self.codes_seen(self.plain), [])

    def test_shared_user_sees_every_review(self):
        self.assertEqual(
            self.codes_seen(self.shared),
            ["AFS - JS", "BIU", "PMX - EU"],
        )

    def test_the_queryset_helper_agrees_with_the_endpoint(self):
        fresh = U.objects.get(pk=self.shared.pk)
        self.assertEqual(
            scope_queryset(PaperReview.objects.all(), fresh).count(), 3)

    def test_it_does_not_widen_any_other_module(self):
        """
        The whole point. Sharing paper review must leave events exactly as scoped
        as they were, or this is is_all_access wearing a narrower label.
        """
        fresh = U.objects.get(pk=self.shared.pk)
        self.assertFalse(has_module_action(fresh, "events", "all"))
        self.client.force_authenticate(user=fresh)
        r = self.client.get(EV_LIST, {"page_size": 50})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(list(r.data["results"]), [])


class SaveRoundTripTests(TestCase):
    """The PUT the permission modal sends, with the fifth cell in it."""

    @classmethod
    def setUpTestData(cls):
        cls.team = Team.objects.create(name="Roles editors", is_all_access=True)
        cls.admin = U.objects.create_user(
            username="ar_admin", password="x", email="ad@x.com",
            role="admin", team=cls.team)
        cls.target = U.objects.create_user(
            username="ar_target", password="x", email="t@x.com", role="sales")

    def test_put_stores_and_clears_the_cell(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        url = f"/api/users/{self.target.id}/permissions/"

        body = {"permissions": [{
            "module": "paper_review",
            "can_view": True, "can_create": None,
            "can_update": None, "can_delete": None, "can_all": True,
        }]}
        r = client.put(url, body, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        row = UserPermission.objects.get(user=self.target, module="paper_review")
        self.assertIs(row.can_all, True)

        # Back to inherit. An all-null row carries no information and is deleted,
        # so this also proves can_all is counted by is_empty().
        body["permissions"][0].update(can_view=None, can_all=None)
        r = client.put(url, body, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(
            UserPermission.objects.filter(
                user=self.target, module="paper_review").exists())
