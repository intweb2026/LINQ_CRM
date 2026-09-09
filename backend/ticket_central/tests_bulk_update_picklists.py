"""
ticket_central/tests_bulk_update_picklists.py
──────────────────────────────────────────────
Every mass-update dropdown must offer values the column actually holds.

THE FAILURE THIS PINS
type_of_ticket, relationship and ticket_type are plain CharFields (the D4 notes
in models.py), so nothing at the database layer constrains them and nothing
catches a wrong option list. The picker was built from the model's enums, whose
values are NOT the stored vocabulary:

    Ticket.TypeOfTicket.values -> "BX"            column holds "Blue - BX"
    Ticket.Relationship.values -> "direct"        column holds "Direct"
    ticket_type                -> no list at all  column holds "Simple"/"Complex"

So the dropdown offered nine options for type_of_ticket of which zero appeared on
any of the 37,008 rows, and applying one rewrote the column into a second,
parallel vocabulary that extract_type_code and the Mining Matrix each read
differently. Silent, and across a thousand rows at a time.

A unit test cannot see the live table, so the assertion is the other way round:
fixtures are written with the spellings confirmed against production, and each
one must be offered. A future edit that swaps a list back to an enum fails here.

The DMD pair is asserted separately — its options are live users, not a literal.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from teams.models import Team, TeamPermission
from ticket_central.models import Ticket
from ticket_central.views import TicketViewSet

User = get_user_model()

SCHEMA = TicketViewSet.as_view({"get": "bulk_update_schema"})

# Confirmed against all 37,008 live rows, 2026-09-08. Two-thirds of the type
# column is "LinkedIn - LX" and "Comp.-CX"; "ZID" carries no code suffix at all,
# which is why this list cannot be derived from the enum by string surgery.
STORED = {
    "type_of_ticket": ["LinkedIn - LX", "Comp.-CX", "White - WH", "Blue - BX",
                       "Green - GR", "Yellow - YL", "Platinum - PX",
                       "Gold - GX", "ZID"],
    "relationship":   ["Direct"],
    "ticket_type":    ["Simple", "Complex"],
    "priority":       ["DD", "AS", "SPEX", "AD", "MEDIA", "ASSOC", "AB"],
}


class TicketPicklistTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        team = Team.objects.create(name="tc_picklist_admin", is_all_access=True)
        TeamPermission.objects.update_or_create(
            team=team, module="ticket_central",
            defaults={"can_view": True, "can_create": True,
                      "can_update": True, "can_delete": True},
        )
        cls.admin = User.objects.create_user(
            username="tc_picklist_admin", email="tc_picklist_admin@iq-hub.com",
            password="x", role=User.Role.ADMIN, team=team,
        )

    def schema(self):
        request = APIRequestFactory().get("/api/tickets/bulk_update_schema/")
        force_authenticate(request, user=self.admin)
        response = SCHEMA(request)
        self.assertEqual(response.status_code, 200)
        return response.data["fields"]

    def test_stored_values_are_offered(self):
        fields = self.schema()
        for name, values in STORED.items():
            with self.subTest(field=name):
                self.assertEqual(fields[name]["type"], "choice")
                missing = [v for v in values if v not in fields[name]["choices"]]
                self.assertEqual(
                    missing, [],
                    f"{name} stores {missing} but the picker does not offer them",
                )

    def test_a_stored_value_survives_a_mass_update(self):
        """
        The end-to-end version of the above: an offered option must be writable
        and come back unchanged. A schema listing "BX" passes the assertion above
        only if "BX" is what the column holds — this proves the round trip on a
        value the live data really carries.
        """
        ticket = Ticket.objects.create(
            purpose="PIK", type_of_ticket="White - WH", status=Ticket.Status.DRAFT,
        )
        bulk = TicketViewSet.as_view({"post": "bulk_update"})

        def post(payload):
            request = APIRequestFactory().post(
                "/api/tickets/bulk_update/", payload, format="json",
            )
            force_authenticate(request, user=self.admin)
            return bulk(request)

        body = {"ids": [ticket.id], "field": "type_of_ticket",
                "value": "Comp.-CX"}
        # Commit echoes back the hash of the plan the caller was shown, or the
        # mixin answers 409 — so the round trip has to run the preview too.
        plan = post({**body, "commit": False})
        self.assertEqual(plan.status_code, 200, plan.data)
        response = post({**body, "commit": True,
                         "plan_hash": plan.data["plan_hash"]})
        self.assertEqual(response.status_code, 200, response.data)
        ticket.refresh_from_db()
        self.assertEqual(ticket.type_of_ticket, "Comp.-CX")

    def test_assign_name_offers_data_mining_users_by_display_name(self):
        """
        The DMD columns store a display name, never an email, so the dropdown
        must offer names — and only from role=data_mining, because these are Data
        Mining assignments. An account created with a name already in the column
        is what "connects" it to those rows; there is no join to populate.
        """
        User.objects.create_user(
            username="dmd_seed", email="dmd.seed@dmd.invalid", password="x",
            first_name="Vanshika", last_name="Parmar",
            role=User.Role.DATA_MINING,
        )
        User.objects.create_user(
            username="mr_seed", email="mr.seed@iq-hub.com", password="x",
            first_name="Not", last_name="Mining", role=User.Role.MARKET_RESEARCH,
        )
        fields = self.schema()
        for key in ("assign_name", "assign_name_lx2"):
            with self.subTest(field=key):
                self.assertEqual(fields[key]["type"], "choice")
                self.assertIn("Vanshika Parmar", fields[key]["choices"])
                self.assertNotIn("Not Mining", fields[key]["choices"])
                self.assertNotIn(
                    "dmd.seed@dmd.invalid", fields[key]["choices"],
                    "the column stores names; offering an email writes a value "
                    "no existing row matches",
                )
