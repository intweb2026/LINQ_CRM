"""
ticket_central/tests_dmd_lead_mr_edit.py
─────────────────────────────────────────
A DMD lead or manager may correct the Market Research half; a plain miner may not.

THE RULE THIS PINS
Ticket Central is a two-phase handover: MR owns Section A, DMD owns Section B,
and TicketDMDUpdateSerializer refuses an MR field outright. That is right for a
miner working the queue and wrong for the person they escalate to — a bad link or
a nonsense estimate used to mean bouncing the ticket back to MR and waiting, or
fetching an admin.

Seniority is read off the two flags the CRM already has, either one being enough:
`is_team_lead` (the tick on the user form) and `is_team_manager` (the
super-admin-assigned managed_team). Role is still checked, so a Sales lead gets
nothing here.

The status window is DELIBERATELY the DMD one, not the MR one. A lead corrects a
brief on a ticket that has reached them; a Draft is MR's to finish.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from teams.models import Team, TeamPermission
from ticket_central.models import Ticket
from ticket_central.views import TicketViewSet

User = get_user_model()

PATCH = TicketViewSet.as_view({"patch": "partial_update"})


class DMDLeadMREditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dmd_team = Team.objects.create(name="Data Mining QA")
        TeamPermission.objects.update_or_create(
            team=cls.dmd_team, module="ticket_central",
            defaults={"can_view": True, "can_create": False,
                      "can_update": True, "can_delete": False},
        )

        def dmd(username, **kw):
            return User.objects.create_user(
                username=username, email=f"{username}@iq-hub.com", password="x",
                role=User.Role.DATA_MINING, team=cls.dmd_team, **kw,
            )

        cls.miner = dmd("dmd_miner")
        cls.lead = dmd("dmd_lead", is_team_lead=True)
        cls.manager = dmd("dmd_manager", managed_team=cls.dmd_team)

    def ticket(self, status=Ticket.Status.MR_SUBMITTED):
        return Ticket.objects.create(
            purpose="PIK", type_of_ticket="White - WH", estimate=100,
            link_url="https://example.com/a", status=status,
        )

    def patch(self, user, ticket, body):
        request = APIRequestFactory().patch(
            f"/api/tickets/{ticket.id}/", body, format="json",
        )
        force_authenticate(request, user=user)
        return PATCH(request, pk=ticket.id)

    # ── The grant ────────────────────────────────────────────────────────────

    def test_lead_may_rewrite_an_mr_field(self):
        ticket = self.ticket()
        response = self.patch(self.lead, ticket, {"estimate": 250})
        self.assertEqual(response.status_code, 200, response.data)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estimate, 250)

    def test_manager_may_rewrite_an_mr_field(self):
        ticket = self.ticket()
        response = self.patch(self.manager, ticket, {"purpose": "PIK corrected"})
        self.assertEqual(response.status_code, 200, response.data)
        ticket.refresh_from_db()
        # Ticket.save() upper-cases purpose; the assertion follows the model
        # rather than fighting it.
        self.assertEqual(ticket.purpose, "PIK CORRECTED")

    def test_lead_keeps_the_dmd_half(self):
        """The grant ADDS the MR section; it must not cost them their own."""
        ticket = self.ticket()
        response = self.patch(self.lead, ticket,
                              {"mined_count": 42, "mr_comments": "link fixed"})
        self.assertEqual(response.status_code, 200, response.data)
        ticket.refresh_from_db()
        self.assertEqual(ticket.mined_count, 42)
        self.assertEqual(ticket.mr_comments, "link fixed")

    # ── The limits ───────────────────────────────────────────────────────────

    def test_plain_miner_is_still_refused(self):
        ticket = self.ticket()
        response = self.patch(self.miner, ticket, {"estimate": 250})
        self.assertEqual(response.status_code, 400)
        self.assertIn("estimate", response.data)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estimate, 100)

    def test_lead_cannot_reach_a_draft(self):
        """Before MR submits, the ticket is not the lead's to edit."""
        ticket = self.ticket(status=Ticket.Status.DRAFT)
        response = self.patch(self.lead, ticket, {"estimate": 250})
        self.assertEqual(response.status_code, 400)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estimate, 100)

    def test_sales_lead_gets_nothing(self):
        """Seniority in another department is not seniority here."""
        from ticket_central.permissions import may_edit_mr_fields
        sales_lead = User.objects.create_user(
            username="sales_lead", email="sales_lead@iq-hub.com", password="x",
            role=User.Role.SALES, is_team_lead=True,
        )
        self.assertFalse(may_edit_mr_fields(sales_lead))
        self.assertFalse(may_edit_mr_fields(self.miner))
        self.assertTrue(may_edit_mr_fields(self.lead))
        self.assertTrue(may_edit_mr_fields(self.manager))

    # ── What the client is told ──────────────────────────────────────────────

    def test_my_permissions_advertises_the_right(self):
        """
        The ticket form draws Section A open or locked off this flag, so a server
        that grants the write and a payload that says otherwise would leave the
        lead staring at fields they are allowed to edit.
        """
        from accounts.views import UserViewSet

        view = UserViewSet.as_view({"get": "my_permissions"})
        for user, expected in ((self.lead, True), (self.manager, True),
                               (self.miner, False)):
            with self.subTest(user=user.username):
                request = APIRequestFactory().get("/api/users/my-permissions/")
                force_authenticate(request, user=user)
                response = view(request)
                self.assertEqual(response.status_code, 200)
                self.assertIs(response.data["may_edit_mr_fields"], expected)
