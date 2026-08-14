"""
accounts/tests_clear_all_gate.py
─────────────────────────────────
Who may empty a module — asserted once, across every module that offers it.

THE RULE
"Clear all data" belongs to the HP account and to nobody else. Not a role=admin
user, not a custom role with is_all_access, not a Django superuser, not the module's
own can_delete holder. Every other permission in this CRM widens with seniority;
this one does not, and that is the whole point of it.

WHY THE SUITE IS TABLE-DRIVEN OVER ALL FIVE ENDPOINTS
The gate used to be an inline `request.user.username != 'HP'` copied into each
module, and two of the five did not exist yet. A per-module test would let the sixth
module ship without one — so the table below is the list of wipe endpoints, and
every caller-shaped test runs against all of them. Adding an endpoint without adding
it here leaves ENDPOINTS out of step with the router, which
test_every_wipe_endpoint_is_covered fails on.

NOTHING SURVIVES A REFUSAL: each denial test asserts the fixture rows are still
there afterwards. A 403 that had already deleted half a table would otherwise pass a
status-code-only check.

    python manage.py test accounts.tests_clear_all_gate
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import get_resolver, reverse
from rest_framework.test import APIClient


from accounts.permissions import HP_USERNAME
from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event
from paper_review.models import PaperReview
from proposal_submission.models import ProposalSubmission
from ticket_central.models import Ticket
from teams.models import Team, TeamPermission

User = get_user_model()

# module label → (url name, the model a successful wipe must empty)
ENDPOINTS = {
    "bookings":            ("invoices-clear-all", BookEvent),
    "events":              ("events-clear-all", Event),
    "ticket_central":      ("tickets-clear-all", Ticket),
    "paper_review":        ("paper-reviews-clear-all", PaperReview),
    "proposal_submission": ("proposal-submissions-clear-all", ProposalSubmission),
}


def make_fixture_rows():
    """One row per module, so "did the wipe run" is answerable per endpoint."""
    event = Event.objects.create(
        event_code="WIPE - AA", official_event_name="Wipe Event",
        event_date="2026-05-05",
    )
    invoice = BookEvent.objects.create(
        invoice_number="WIPE-1", event_code="WIPE - AA", payment_status="Paid",
    )
    BookDelegate.objects.create(
        invoice=invoice, event_code="WIPE - AA",
        first_name="Wipe", last_name="Me", email="wipe@acme.test",
    )
    Ticket.objects.create(purpose="AS", type_of_ticket="Blue - BX")
    PaperReview.objects.create(event_code="WIPE - AA", speaker_name="Spk",
                               email="spk@acme.test")
    ProposalSubmission.objects.create(event_code="WIPE - AA", speaker_name="Spk",
                                      email="spk@acme.test")
    return event


class ClearAllGateTests(TestCase):
    """Every caller who is NOT HP, against every wipe endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.all_access = Team.objects.create(
            name="wipe_all_access", is_all_access=True,
        )
        cls.full_crud = Team.objects.create(
            name="wipe_full_crud",
        )
        for module in ENDPOINTS:
            TeamPermission.objects.create(
                team=cls.full_crud, module=module,
                can_view=True, can_create=True, can_update=True, can_delete=True,
            )

        cls.hp = User.objects.create_user(
            username=HP_USERNAME, password="x", email="hp@iq-hub.com", role="admin",
        )

        # Every kind of caller who might reasonably expect to be allowed.
        cls.admin_role = User.objects.create_user(
            username="wipe_admin", password="x", email="a@iq-hub.com", role="admin",
        )
        cls.admin_role.team = cls.all_access
        cls.admin_role.save()

        cls.superuser = User.objects.create_superuser(
            username="wipe_super", password="x", email="s@iq-hub.com",
        )
        cls.superuser.team = cls.all_access
        cls.superuser.save()

        cls.deleter = User.objects.create_user(
            username="wipe_deleter", password="x", email="d@iq-hub.com", role="sales",
        )
        cls.deleter.team = cls.full_crud
        cls.deleter.save()

        cls.nobody = User.objects.create_user(
            username="wipe_nobody", password="x", email="n@iq-hub.com", role="sales",
        )

    def setUp(self):
        self.client = APIClient()
        make_fixture_rows()

    def counts(self):
        return {label: model.objects.count() for label, (_, model) in ENDPOINTS.items()}

    def assert_all_refused(self, user, label):
        """`user` is refused by EVERY wipe endpoint, and nothing is deleted."""
        before = self.counts()
        self.client.force_authenticate(user=user)
        for module, (url_name, _) in ENDPOINTS.items():
            resp = self.client.delete(reverse(url_name))
            self.assertEqual(
                resp.status_code, 403,
                f"{label} was not refused by {module}: {resp.status_code} {resp.content}",
            )
        self.assertEqual(
            self.counts(), before,
            f"{label} was refused but data was deleted anyway",
        )

    def test_an_all_access_admin_is_refused(self):
        """The closest thing this CRM has to a super admin. Still not HP."""
        self.assert_all_refused(self.admin_role, "role=admin + is_all_access")

    def test_a_django_superuser_is_refused(self):
        self.assert_all_refused(self.superuser, "is_superuser")

    def test_a_role_holding_can_delete_on_every_module_is_refused(self):
        """
        can_delete is per-record deletion. Emptying the module is not the same right,
        and a role granted the former must not acquire the latter.
        """
        self.assert_all_refused(self.deleter, "can_delete on every module")

    def test_a_user_with_no_role_is_refused(self):
        self.assert_all_refused(self.nobody, "no custom role")

    def test_an_anonymous_caller_is_refused(self):
        before = self.counts()
        for module, (url_name, _) in ENDPOINTS.items():
            resp = self.client.delete(reverse(url_name))
            self.assertIn(
                resp.status_code, (401, 403),
                f"anonymous reached {module}: {resp.status_code}",
            )
        self.assertEqual(self.counts(), before)

    def test_the_refusal_names_the_account(self):
        """A 403 whose body says nothing sends the reader to the logs for no reason."""
        self.client.force_authenticate(user=self.admin_role)
        resp = self.client.delete(reverse("invoices-clear-all"))
        self.assertEqual(resp.status_code, 403)
        self.assertIn("HP", str(resp.data.get("detail", "")))

    def test_every_wipe_endpoint_is_covered(self):
        """
        ENDPOINTS must list every clear_all route the router exposes. Otherwise a new
        module's wipe ships untested, which is exactly how two of these came to exist
        with no gate test at all.
        """
        registered = {
            str(pattern.pattern) for pattern in get_resolver().url_patterns
        }
        # The router flattens into api/, so walk the include and match on url_path.
        found = set()
        for entry in get_resolver().url_patterns:
            for sub in getattr(entry, "url_patterns", []):
                text = str(sub.pattern)
                if "clear_all" in text:
                    found.add(sub.name)
        self.assertEqual(
            found, {url_name for url_name, _ in ENDPOINTS.values()},
            "a clear_all route exists that this suite does not cover (or vice versa)",
        )
        self.assertTrue(registered)  # resolver walked, not silently empty


class ClearAllAsHPTests(TestCase):
    """HP is allowed, and the wipe actually empties the module it names."""

    @classmethod
    def setUpTestData(cls):
        cls.hp = User.objects.create_user(
            username=HP_USERNAME, password="x", email="hp@iq-hub.com", role="admin",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.hp)
        make_fixture_rows()

    def test_each_endpoint_empties_its_own_module(self):
        for module, (url_name, model) in ENDPOINTS.items():
            with self.subTest(module=module):
                self.assertEqual(model.objects.count(), 1, f"{module} fixture missing")
                resp = self.client.delete(reverse(url_name))
                self.assertIn(resp.status_code, (200, 204), resp.content)
                self.assertEqual(model.objects.count(), 0, f"{module} was not emptied")

    def test_clearing_bookings_takes_the_delegates_with_it(self):
        self.assertEqual(BookDelegate.objects.count(), 1)
        self.client.delete(reverse("invoices-clear-all"))
        self.assertEqual(BookDelegate.objects.count(), 0)

    def test_clearing_events_leaves_bookings_alone(self):
        """
        BookEvent.event_code is text, not an FK, so the catalogue and the bookings are
        independent. Asserted because the UI's confirmation promises exactly this.
        """
        self.client.delete(reverse("events-clear-all"))
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(BookEvent.objects.count(), 1)

    def test_clearing_paper_reviews_unlinks_proposals_rather_than_deleting_them(self):
        review = PaperReview.objects.first()
        proposal = ProposalSubmission.objects.first()
        proposal.source_paper_review = review
        proposal.save(update_fields=["source_paper_review"])

        resp = self.client.delete(reverse("paper-reviews-clear-all"))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["proposals_unlinked"], 1)

        proposal.refresh_from_db()
        self.assertIsNone(proposal.source_paper_review_id)
        self.assertEqual(ProposalSubmission.objects.count(), 1)

    def test_clearing_proposals_leaves_paper_reviews_alone(self):
        self.client.delete(reverse("proposal-submissions-clear-all"))
        self.assertEqual(ProposalSubmission.objects.count(), 0)
        self.assertEqual(PaperReview.objects.count(), 1)

    def test_clearing_tickets_resets_the_number_sequences(self):
        from ticket_central.models import TicketSequence
        TicketSequence.objects.create(purpose_key="AS-BX", last_number=10041)
        resp = self.client.delete(reverse("tickets-clear-all"))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(TicketSequence.objects.count(), 0)
        self.assertTrue(resp.data["sequences_reset"])

    def test_every_wipe_is_written_to_the_action_log(self):
        """
        An irreversible action with no record of who ran it is the one case where a
        missing audit entry is itself the incident.
        """
        from accounts.models import ActionLog
        for module, (url_name, _) in ENDPOINTS.items():
            with self.subTest(module=module):
                ActionLog.objects.all().delete()
                self.client.delete(reverse(url_name))
                log = ActionLog.objects.filter(action__startswith="CLEARED ALL").first()
                self.assertIsNotNone(log, f"{module} logged nothing")
                self.assertEqual(log.user_id, self.hp.id)

    def test_a_wipe_reports_what_it_deleted(self):
        resp = self.client.delete(reverse("invoices-clear-all"))
        self.assertEqual(resp.data["deleted"]["invoices"], 1)
        self.assertEqual(resp.data["deleted"]["delegates"], 1)
