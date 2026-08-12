"""
paper_review/tests.py
──────────────────────
Shared fixtures for the app's suites, plus the endpoint contract the frontend
(frontend/src/api/paperReview.js) was written against.

The two workflow suites live beside this file and import _Base from it, the same
way proposal_submission splits tests.py / tests_gaps.py / tests_extras.py:

    tests_paper_to_proposal.py   PART A — auto-created ProposalSubmission
    tests_notification.py        PART B — production-team notification
"""
from datetime import date

from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import CustomRole, RolePermission
from events.models import Event
from paper_review.models import PaperReview

# Nothing in this suite may reach a real mailbox. locmem is asserted rather than
# assumed, and every address below is under example.com / .invalid.
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
ALERT = "crm-alerts@example.invalid"


def make_event(code, name="Some Event", event_date=date(2026, 5, 1), **extra):
    return Event.objects.create(
        event_code=code, official_event_name=name, event_date=event_date, **extra,
    )


@override_settings(EMAIL_BACKEND=LOCMEM, DEFAULT_FROM_EMAIL="crm@example.com",
                   PAPER_REVIEW_ALERT_EMAIL=ALERT,
                   # PAPER_REVIEW_NOTIFICATIONS_ENABLED defaults False in
                   # production (see B1) — the whole suite exercises the ENABLED
                   # path so the notification behaviour keeps getting proven; the
                   # disabled/default path has its own suite in
                   # tests_notifications_disabled.py.
                   PAPER_REVIEW_NOTIFICATIONS_ENABLED=True)
class _Base(APITestCase):
    LIST = "/api/paper-reviews/"
    PROPOSALS = "/api/proposal-submissions/"

    @classmethod
    def assign_events(cls, *events):
        cls.user.assigned_events.add(*events)

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        U = get_user_model()

        # The event the whole suite files reviews against. sales_executive is the
        # Part B To recipient; the two users added to assigned_users below are the
        # Cc recipients.
        cls.sales_exec = U.objects.create_user(
            username="pr_sales_exec", password="x", role="sales",
            email="sales.exec@example.com", first_name="Sam", last_name="Exec",
        )
        cls.event = make_event("AFS - JS", "Aviation Fuel Summit 2026")
        cls.event.sales_executive = cls.sales_exec
        cls.event.save()
        cls.other_event = make_event("BIUK - PM", "EV Charging UK 2026")

        cls.cc_speaker_sales = U.objects.create_user(
            username="pr_spk_sales", password="x", role="speaker_sales",
            email="speaker.sales@example.com",
        )
        cls.cc_market_research = U.objects.create_user(
            username="pr_mr", password="x", role="market_research",
            email="market.research@example.com",
        )
        cls.cc_speaker_sales.assigned_events.set([cls.event])
        cls.cc_market_research.assigned_events.set([cls.event])

        # The author. Granted BOTH modules: A8 asserts the generated proposal is
        # immediately visible to them, which is a row-scope claim tested through
        # the proposal endpoint, so the module grant has to be there for the
        # request to get that far.
        cls.role = CustomRole.objects.create(name="Reviews Full")
        for module in ("paper_review", "proposal_submission"):
            RolePermission.objects.create(
                custom_role=cls.role, module=module,
                can_view=True, can_create=True, can_update=True, can_delete=True,
            )
        # role="sales" deliberately: a SCOPED, non-MR, non-admin author, so the
        # suite exercises the ordinary path — and one who is NOT himself a Part B
        # Cc recipient, since "sales" is not in notifications.CC_ROLES.
        cls.user = U.objects.create_user(
            username="pr_author", password="x", role="sales",
            email="author@example.com", custom_role=cls.role,
        )
        cls.user.assigned_events.set([cls.event, cls.other_event])

        cls.blind_role = CustomRole.objects.create(name="No Reviews")
        RolePermission.objects.create(
            custom_role=cls.blind_role, module="paper_review",
            can_view=False, can_create=False, can_update=False, can_delete=False,
        )
        cls.blind_user = U.objects.create_user(
            username="pr_blind", password="x", email="blind@example.com",
            custom_role=cls.blind_role,
        )

    def payload(self, **over):
        base = {
            "paper_submission_date": "2026-08-10",
            "event_code": "AFS - JS",
            "speaker_name": "Eli Jasso",
            "company_name": "Cicada Logistics",
            "email": "eli.jasso@example.com",
            "linkedin_speaker": "https://www.linkedin.com/in/eli-jasso-a4067396/",
            "linkedin_company": "https://www.linkedin.com/company/cicada-logistics/",
            "linkedin_followers": 417,
            "nos": True,
            "closeness_to_topic": 9,
            "closeness_to_region": 2,
            "clear_solution_to_challenges": 9,
            "case_study_results_examples": 1,
            "not_obvious_sales_pitch": 1,
            "company_profile_score": 5,
            "grade": "B",
            "session_location_on_agenda": "Day 1, Afternoon Session",
            # Blank on purpose: cls.user is deliberately NOT Market Research, and
            # the serializer refuses real MR content from a non-MR author (it only
            # drops a blank/unchanged echo). Suites that need internal_footnotes
            # populated set it through the ORM — see tests_notification.py.
            "internal_footnotes": "",
            "feedback_to_speaker": "Please add a case study.",
            "proposal_received": "Terminal and rail decarbonisation",
            "theme": "terminal and rail environment",
            "agenda_addition": "CHALLENGES IN OILFIELD CULTURE\n- one\n- two",
        }
        base.update(over)
        return base

    def create_review(self, **over):
        """POST a review with on_commit callbacks executed, and return the response."""
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.LIST, self.payload(**over), format="json")
        return response


class EndpointContractTests(_Base):
    """The five routes frontend/src/api/paperReview.js calls."""

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_create_persists_the_review_and_computes_the_score(self):
        r = self.create_review()
        self.assertEqual(r.status_code, 201, r.content)
        review = PaperReview.objects.get(id=r.data["id"])
        self.assertEqual(review.speaker_name, "Eli Jasso")
        # 9 + 2 + 9 + 1 + 1 + 5 — the verified reference record.
        self.assertEqual(review.proposal_score, 27)
        self.assertEqual(review.created_by, self.user)

    def test_list_uses_the_standard_pagination_envelope(self):
        self.create_review()
        r = self.client.get(self.LIST)
        self.assertEqual(r.status_code, 200)
        for key in ("count", "total_pages", "page", "page_size",
                    "next", "previous", "results"):
            self.assertIn(key, r.data, f"{key} missing from list envelope")

    def test_retrieve_patch_and_delete(self):
        rid = self.create_review().data["id"]
        self.assertEqual(self.client.get(f"{self.LIST}{rid}/").status_code, 200)
        r = self.client.patch(f"{self.LIST}{rid}/",
                              {"speaker_name": "Eli J."}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.client.delete(f"{self.LIST}{rid}/").status_code, 204)
        self.assertFalse(PaperReview.objects.filter(id=rid).exists())

    def test_module_gate_refuses_a_role_without_the_grant(self):
        self.client.force_authenticate(user=self.blind_user)
        self.assertEqual(self.client.get(self.LIST).status_code, 403)

    def test_a_review_outside_the_users_events_is_refused(self):
        other = make_event("ZZZ - QQ", "Unassigned Event")
        r = self.client.post(self.LIST, self.payload(event_code=other.event_code),
                             format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("event_code", r.data)

    def test_rows_outside_scope_are_invisible(self):
        PaperReview.objects.create(
            event_code="ZZZ - QQ", speaker_name="Hidden",
            email="hidden@example.com", paper_submission_date=date(2026, 4, 1),
        )
        r = self.client.get(self.LIST)
        self.assertEqual(
            [row["event_code"] for row in r.data["results"]
             if row["event_code"] == "ZZZ - QQ"], [])

    def test_internal_footnotes_is_stripped_for_a_non_mr_author(self):
        rid = self.create_review().data["id"]
        row = self.client.get(f"{self.LIST}{rid}/").data
        self.assertNotIn("internal_footnotes", row)

    def test_ordering_by_an_mr_field_is_refused(self):
        r = self.client.get(self.LIST, {"ordering": "internal_footnotes"})
        self.assertEqual(r.status_code, 400)
