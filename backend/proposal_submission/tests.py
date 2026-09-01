"""
proposal_submission/tests.py
─────────────────────────────
Covers the contract the frontend depends on: the five endpoints, the pagination
envelope, event_code enforcement, the permission gate, and the decisions that
were guesses in the spec (submission_date defaulting, unconstrained picklists).
"""
from datetime import date

from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import ActionLog
from events.models import Event
from events.testutils import assign_reviewer
from proposal_submission.models import ProposalSubmission
from teams.models import Team, TeamPermission

User = None  # bound in setUpTestData via get_user_model


def make_event(code, name="Some Event", event_date=date(2026, 5, 1)):
    return Event.objects.create(
        event_code=code, official_event_name=name, event_date=event_date,
    )


class _Base(APITestCase):
    LIST = "/api/proposal-submissions/"

    @classmethod
    def assign_events(cls, *events):
        """Widen cls.user's scope to cover events a subclass created."""
        assign_reviewer(cls.user, *events)

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        U = get_user_model()
        cls.event = make_event("AFS - JS", "Aviation Fuel Summit 2026")
        cls.other_event = make_event("BIUK - PM", "EV Charging UK 2026")

        # A role with full proposal_submission rights.
        cls.role = Team.objects.create(name="Proposals Full")
        TeamPermission.objects.create(
            team=cls.role, module="proposal_submission",
            can_view=True, can_create=True, can_update=True, can_delete=True,
        )
        cls.user = U.objects.create_user(
            username="prop_user", password="x", email="p@example.com",
            team=cls.role,
        )
        # cls.user is a SCOPED user on purpose — not admin, not all-access, not
        # market_research — so the rest of the suite exercises the ordinary path.
        # Access derives solely from this assignment, so every event a test uses
        # has to be assigned here (or via assign_events in a subclass).
        assign_reviewer(cls.user, cls.event, cls.other_event)

        # A role with no proposal_submission grant at all.
        cls.blind_role = Team.objects.create(name="No Proposals")
        TeamPermission.objects.create(
            team=cls.blind_role, module="proposal_submission",
            can_view=False, can_create=False, can_update=False, can_delete=False,
        )
        cls.blind_user = U.objects.create_user(
            username="blind_user", password="x", email="b@example.com",
            team=cls.blind_role,
        )

    def payload(self, **over):
        base = {
            "event_code": "AFS - JS",
            "submission_date": "2026-08-10",
            "participation_type": "Speaker",
            "speaker_name": "Eli Jasso",
            "email": "eli.jasso@cicadalogistics.co",
            "company_name": "Cicada Logistics",
            "qc_grade": "B",
            "qc_score": 27,
            "sales_pitch_factor": "",
            "presentation_theme": "terminal and rail environment",
            "linkedin_speaker": "https://www.linkedin.com/in/eli-jasso-a4067396/",
            "linkedin_company": "",
            "linkedin_followers": 417,
            "speaker_slot_status": "",
            "sponsorship_status": "",
            "spex_remarks": "",
            "agenda_slot": "Day 1, Afternoon Session",
            "revenue_possibility": "",
            "internal_footnotes_mr": "",
            "slot_recommendation_mr": "",
            "agenda_addition": "CHALLENGES IN OILFIELD CULTURE\n- one\n- two",
        }
        base.update(over)
        return base


class CrudTests(_Base):
    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_create_returns_201_and_persists_every_field(self):
        r = self.client.post(self.LIST, self.payload(), format="json")
        self.assertEqual(r.status_code, 201, r.content)
        p = ProposalSubmission.objects.get(id=r.data["id"])
        self.assertEqual(p.speaker_name, "Eli Jasso")
        self.assertEqual(p.qc_score, 27)
        self.assertEqual(p.linkedin_followers, 417)
        self.assertEqual(p.agenda_slot, "Day 1, Afternoon Session")
        self.assertEqual(p.created_by, self.user)

    def test_list_uses_the_standard_pagination_envelope(self):
        self.client.post(self.LIST, self.payload(), format="json")
        r = self.client.get(self.LIST)
        self.assertEqual(r.status_code, 200)
        for key in ("count", "total_pages", "page", "page_size",
                    "next", "previous", "results"):
            self.assertIn(key, r.data, f"{key} missing from list envelope")
        self.assertEqual(r.data["page_size"], 50)

    def test_retrieve_patch_and_delete(self):
        pid = self.client.post(self.LIST, self.payload(), format="json").data["id"]
        detail = f"{self.LIST}{pid}/"

        self.assertEqual(self.client.get(detail).status_code, 200)

        r = self.client.patch(detail, {"speaker_slot_status": "Confirmed"},
                              format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(
            ProposalSubmission.objects.get(id=pid).speaker_slot_status, "Confirmed")
        self.assertEqual(ProposalSubmission.objects.get(id=pid).updated_by, self.user)

        r = self.client.delete(detail)
        self.assertEqual(r.status_code, 204)
        self.assertFalse(ProposalSubmission.objects.filter(id=pid).exists())

    def test_every_write_is_logged_to_actionlog(self):
        before = ActionLog.objects.count()
        pid = self.client.post(self.LIST, self.payload(), format="json").data["id"]
        self.client.patch(f"{self.LIST}{pid}/", {"qc_grade": "A"}, format="json")
        self.client.delete(f"{self.LIST}{pid}/")
        self.assertEqual(ActionLog.objects.count(), before + 3)
        actions = list(ActionLog.objects.order_by("-id")[:3]
                       .values_list("action", flat=True))
        self.assertTrue(any("DELETED proposal submission" in a for a in actions))

    def test_event_name_is_resolved_for_display(self):
        r = self.client.post(self.LIST, self.payload(), format="json")
        self.assertEqual(r.data["event_name"], "Aviation Fuel Summit 2026")


class ValidationTests(_Base):
    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_unknown_event_code_is_rejected_with_a_field_error(self):
        r = self.client.post(self.LIST, self.payload(event_code="NOPE - ZZ"),
                             format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("event_code", r.data)

    def test_event_code_is_matched_case_insensitively_and_stored_canonically(self):
        r = self.client.post(self.LIST, self.payload(event_code="afs - js"),
                             format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(
            ProposalSubmission.objects.get(id=r.data["id"]).event_code, "AFS - JS")

    def test_required_fields(self):
        for field in ("event_code", "speaker_name", "email"):
            with self.subTest(field=field):
                body = self.payload()
                body.pop(field)
                r = self.client.post(self.LIST, body, format="json")
                self.assertEqual(r.status_code, 400, f"{field} should be required")
                self.assertIn(field, r.data)

    def test_blank_speaker_name_is_rejected(self):
        r = self.client.post(self.LIST, self.payload(speaker_name="   "),
                             format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("speaker_name", r.data)

    def test_invalid_email_is_rejected(self):
        r = self.client.post(self.LIST, self.payload(email="not-an-email"),
                             format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("email", r.data)

    def test_negative_numbers_are_rejected(self):
        for field in ("qc_score", "linkedin_followers"):
            with self.subTest(field=field):
                r = self.client.post(self.LIST, self.payload(**{field: -1}),
                                     format="json")
                self.assertEqual(r.status_code, 400)
                self.assertIn(field, r.data)

    def test_null_numbers_are_accepted(self):
        r = self.client.post(
            self.LIST, self.payload(qc_score=None, linkedin_followers=None),
            format="json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_submission_date_defaults_to_today_when_omitted(self):
        body = self.payload()
        body.pop("submission_date")
        r = self.client.post(self.LIST, body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        # business_today(), not timezone.localdate() and not date.today(): the
        # default resolves in Asia/Kolkata because the team works in IST, while
        # TIME_ZONE stays UTC. See tests_scope-adjacent coverage in
        # tests_extras.py for the frozen-instant proof.
        from proposal_submission.serializers import business_today
        self.assertEqual(str(r.data["submission_date"]), str(business_today()))

    def test_picklists_accept_values_outside_the_frontend_placeholder_set(self):
        """
        The real Zoho picklists are unconfirmed, so nothing may hard-reject an
        unknown value — that is the whole reason these are choice-less
        CharFields. If this test starts failing, someone added choices=.
        """
        r = self.client.post(self.LIST, self.payload(
            participation_type="Keynote Panel",
            qc_grade="A+",
            speaker_slot_status="Provisionally Held",
            sponsorship_status="In Negotiation",
            revenue_possibility="Very High",
        ), format="json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_invalid_url_is_rejected(self):
        r = self.client.post(self.LIST, self.payload(linkedin_speaker="not a url"),
                             format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("linkedin_speaker", r.data)


class PermissionTests(_Base):
    def test_anonymous_is_denied(self):
        self.assertEqual(self.client.get(self.LIST).status_code, 401)

    def test_role_without_the_module_is_denied(self):
        self.client.force_authenticate(user=self.blind_user)
        self.assertEqual(self.client.get(self.LIST).status_code, 403)
        self.assertEqual(
            self.client.post(self.LIST, self.payload(), format="json").status_code,
            403)


class FilterSearchOrderingTests(_Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Alpha One",
            email="a@x.com", company_name="Acme",
            submission_date=date(2026, 1, 1), qc_score=10, qc_grade="A",
            participation_type="Speaker", speaker_slot_status="Confirmed",
        )
        ProposalSubmission.objects.create(
            event_code="BIUK - PM", speaker_name="Beta Two",
            email="b@x.com", company_name="Globex",
            submission_date=date(2026, 3, 1), qc_score=40, qc_grade="B",
            participation_type="Sponsor", speaker_slot_status="Pending",
        )

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_filter_by_event_code(self):
        r = self.client.get(self.LIST, {"event_code": "BIUK"})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["speaker_name"], "Beta Two")

    def test_filter_by_dropdown_columns(self):
        for field, value, expected in (
            ("participation_type", "Speaker", "Alpha One"),
            ("qc_grade", "b", "Beta Two"),               # iexact
            ("speaker_slot_status", "Confirmed", "Alpha One"),
        ):
            with self.subTest(field=field):
                r = self.client.get(self.LIST, {field: value})
                self.assertEqual(r.data["count"], 1, f"{field}={value}")
                self.assertEqual(r.data["results"][0]["speaker_name"], expected)

    def test_search_spans_the_declared_fields(self):
        self.assertEqual(self.client.get(self.LIST, {"search": "Globex"}).data["count"], 1)
        self.assertEqual(self.client.get(self.LIST, {"search": "Alpha"}).data["count"], 1)

    def test_qc_score_range(self):
        r = self.client.get(self.LIST, {"qc_score_min": 20})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["speaker_name"], "Beta Two")

    def test_submission_date_range(self):
        r = self.client.get(self.LIST, {"submission_date_from": "2026-02-01"})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["speaker_name"], "Beta Two")

    def test_default_ordering_is_submission_date_desc(self):
        r = self.client.get(self.LIST)
        names = [row["speaker_name"] for row in r.data["results"]]
        self.assertEqual(names, ["Beta Two", "Alpha One"])

    def test_user_selected_ordering_gets_the_pk_tiebreaker(self):
        """
        Paging a non-unique sort without a tiebreaker both repeats and skips
        rows. StableOrderingFilter must reach this endpoint too.
        """
        from rest_framework.test import APIRequestFactory
        from rest_framework.request import Request
        from accounts.ordering import StableOrderingFilter
        from proposal_submission.views import ProposalSubmissionViewSet

        view = ProposalSubmissionViewSet()
        for param in ("qc_grade", "-submission_date", None):
            with self.subTest(ordering=param):
                query = {"ordering": param} if param else {}
                # DRF's OrderingFilter reads request.query_params, which only a
                # DRF Request exposes — a bare WSGIRequest is not enough.
                req = Request(APIRequestFactory().get(self.LIST, query))
                view.request = req
                ordering = StableOrderingFilter().get_ordering(
                    req, ProposalSubmission.objects.all(), view)
                self.assertEqual(ordering[-1], "pk",
                                 f"no pk tiebreaker in {ordering}")
