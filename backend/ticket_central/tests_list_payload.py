"""
ticket_central/tests_list_payload.py
─────────────────────────────────────
The list endpoint IS the Ticket Central UI's read model.

The table and the ticket form both render straight off /api/tickets/ rows — the
frontend never fetches the detail route — so a field missing from
TicketListSerializer is not a smaller payload, it is a column that renders blank
and a form field that silently loses whatever the user typed into it on save
(the form diffs against what it was given, so a field it never received looks
unchanged and is never sent).

That failure is invisible from the backend: the request still 200s. These tests
pin the payload to the columns and form fields that exist, so dropping one fails
here instead of in the browser.
"""
from django.utils import timezone
from rest_framework.test import APITestCase

from .constants import DMD_FIELDS, MR_FIELDS
from .tests import auth, make_ticket, make_user

# Every field the Ticket Central table has a column for, or the ticket form an
# input for, beyond the two section sets below.
UI_RECORD_FIELDS = [
    "id", "ticket_number", "status", "event_code",
    "created_at",        # "Added Time" column, and the default sort
    "updated_at",        # "Modified Time" column
    "added_user_text",   # "Added User" column — Zoho's Added User (D16)
    "return_reason",     # the workflow trail's reason for a returned ticket
    "returned_at",
    "created_by_name", "mr_submitted_by_name", "mr_submitted_at", "dmd_submitted_at",
]


class ListPayloadContractTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.mr = make_user("payload_mr", "market_research")

    def _row(self):
        make_ticket(
            purpose="Payload", type_of_ticket="Blue - BX",
            status="mr_submitted", mr_submitted_at=timezone.now(),
            source_spreadsheet_id="sheet-1", source_tab="General",
            source_row_number=726, idempotency_key="sheet-1|General|726",
            added_user_text="zoho_linq-corporate",
        )
        auth(self.client, self.mr)
        resp = self.client.get("/api/tickets/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 1)
        return resp.data["results"][0]

    def test_list_row_carries_every_mr_field(self):
        row = self._row()
        missing = sorted(f for f in MR_FIELDS if f not in row)
        self.assertEqual(missing, [], f"MR fields absent from the list payload: {missing}")

    def test_list_row_carries_every_dmd_field(self):
        """
        Includes source_spreadsheet_id / source_tab / source_row_number /
        idempotency_key. They are import provenance, not work product, but the
        Zoho report shows them and so does this table.
        """
        row = self._row()
        missing = sorted(f for f in DMD_FIELDS if f not in row)
        self.assertEqual(missing, [], f"DMD fields absent from the list payload: {missing}")

    def test_list_row_carries_the_record_fields(self):
        row = self._row()
        missing = [f for f in UI_RECORD_FIELDS if f not in row]
        self.assertEqual(missing, [], f"record fields absent from the list payload: {missing}")

    def test_provenance_values_round_trip(self):
        """Present is not the same as populated — these four came back empty once."""
        row = self._row()
        self.assertEqual(row["source_spreadsheet_id"], "sheet-1")
        self.assertEqual(row["source_tab"], "General")
        self.assertEqual(row["source_row_number"], 726)
        self.assertEqual(row["idempotency_key"], "sheet-1|General|726")
        self.assertEqual(row["added_user_text"], "zoho_linq-corporate")

    def test_dates_are_plain_iso_days(self):
        """
        The form binds these to <input type="date">, which accepts only
        YYYY-MM-DD; anything else lands in the browser as an empty date box.
        """
        make_ticket(
            purpose="Dated", type_of_ticket="Blue - BX",
            status="mr_submitted", mr_submitted_at=timezone.now(),
            assign_date="2026-03-19", complete_date="2026-03-19",
            event_month_year="2026-01-01", hubspot_entry_date="2026-04-01",
            complete_date_lx2="2026-05-02",
        )
        auth(self.client, self.mr)
        row = self.client.get("/api/tickets/").data["results"][0]
        for f in ("assign_date", "complete_date", "event_month_year",
                  "hubspot_entry_date", "complete_date_lx2"):
            self.assertRegex(str(row[f]), r"^\d{4}-\d{2}-\d{2}$", f)
