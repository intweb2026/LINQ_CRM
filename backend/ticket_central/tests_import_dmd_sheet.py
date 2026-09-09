"""
tests_import_dmd_sheet
──────────────────────
The five behaviours import_dmd_sheet exists for, each of which would silently
corrupt data if it regressed.

Fed a CSV rather than a workbook; read_import_rows takes both, and a CSV is a
string the test can write inline instead of a binary fixture nobody can read in
a diff.
"""
import io
from datetime import date

from django.core.management import call_command
from django.test import TestCase

from ticket_central.models import Ticket
from ticket_central.management.commands.import_dmd_sheet import (
    VERDICT_NEW, VERDICT_UNCHANGED, VERDICT_UPDATE, plan, read_sheet,
)

HEADER = ("Entry Date,Contact Purpose,LinkedIn Search Link,LinkedIn Keywords Used,"
          "Estimate No.,Type of Ticket,DM Comments,Assign name,Assign Date,"
          "New Entry,Total,Complete Date\n")


class ImportDmdSheetTests(TestCase):

    def _sheet(self, *lines):
        path = self.mktemp()
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(HEADER)
            for line in lines:
                fh.write(line + "\n")
        return path

    def mktemp(self):
        import tempfile

        handle = tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, mode="w", encoding="utf-8")
        handle.close()
        self.addCleanup(lambda: __import__("os").unlink(handle.name))
        return handle.name

    def test_matches_on_the_link_however_it_is_written(self):
        """
        A stored link and a sheet link that differ only in scheme, "www." and a
        trailing slash are the same page, so the row must update rather than
        create a second ticket.
        """
        ticket = Ticket.objects.create(
            purpose="TIU", link_url="https://www.example.com/search?q=1/",
            type_of_ticket="LinkedIn - LX", status="mr_submitted",
        )
        path = self._sheet(
            "2026-08-14,TIU,http://example.com/search?q=1,peptide CMC,,"
            "LinkedIn - LX,LX-1,SC - Aastha Bhatt,2026-08-18,,,")
        rows, _ = read_sheet(path)
        entry = plan(rows)[0]
        self.assertEqual(entry["verdict"], VERDICT_UPDATE)
        self.assertEqual(entry["ticket"]["id"], ticket.id)
        # The prefix comes off, or the row leaves that person's dropdown.
        self.assertEqual(entry["payload"]["assign_name"], "Aastha Bhatt")

    def test_a_blank_cell_never_clears_a_stored_value(self):
        """
        Every cell the sheet leaves empty is a column the stored ticket keeps.
        The row below states only what the ticket already holds, so it is a
        no-op and must not even be written.
        """
        ticket = Ticket.objects.create(
            purpose="TIU", link_url="https://example.com/a", estimate=42,
            linkedin_keywords="kw", dm_comments="keep me",
            assign_name="Vrunda Rai", assign_date=date(2026, 8, 1),
            actual_number=9, status="completed",
        )
        path = self._sheet("2026-08-14,TIU,https://example.com/a,kw,,,,,,,,")
        entry = plan(read_sheet(path)[0])[0]
        self.assertEqual(entry["verdict"], VERDICT_UNCHANGED)
        self.assertEqual(entry["payload"], {})

        call_command("import_dmd_sheet", path, "--commit",
                     stdout=io.StringIO())
        ticket.refresh_from_db()
        self.assertEqual(ticket.dm_comments, "keep me")
        self.assertEqual(ticket.assign_name, "Vrunda Rai")
        self.assertEqual(ticket.assign_date, date(2026, 8, 1))
        self.assertEqual(ticket.estimate, 42)
        self.assertEqual(ticket.actual_number, 9)

    def test_a_total_completes_the_ticket_and_nothing_moves_it_back(self):
        done = Ticket.objects.create(
            purpose="TIU", link_url="https://example.com/b", status="completed",
            actual_number=10,
        )
        pending = Ticket.objects.create(
            purpose="TIU", link_url="https://example.com/c",
            status="mr_submitted",
        )
        path = self._sheet(
            # b is already completed and the sheet reports no result
            "2026-08-14,TIU,https://example.com/b,kw,,,,,,,,",
            # c now carries a Total, so it is worked
            "2026-08-14,TIU,https://example.com/c,kw,,,,,,5,7,2026-09-01",
        )
        first, second = plan(read_sheet(path)[0])
        self.assertNotIn("status", first["payload"])
        self.assertEqual(second["payload"]["status"], "completed")
        self.assertIsNotNone(second["payload"]["dmd_submitted_at"])

        call_command("import_dmd_sheet", path, "--commit",
                     stdout=io.StringIO())
        done.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(done.status, "completed")
        self.assertEqual(pending.status, "completed")
        self.assertEqual(pending.actual_number, 7)
        self.assertEqual(pending.complete_date, date(2026, 9, 1))

    def test_a_new_row_keeps_its_entry_date_and_gets_a_number(self):
        path = self._sheet(
            "2026-06-15,CNZ,https://example.com/new,rare earths,296,"
            "LinkedIn - LX,LX-CNZ-1,SC - Vrunda Rai,2026-08-18,,,")
        entry = plan(read_sheet(path)[0])[0]
        self.assertEqual(entry["verdict"], VERDICT_NEW)

        call_command("import_dmd_sheet", path, "--commit",
                     stdout=io.StringIO())
        ticket = Ticket.objects.get(purpose="CNZ")
        # Entry Date, not the moment of the import.
        self.assertEqual(ticket.created_at.date(), date(2026, 6, 15))
        self.assertEqual(ticket.ticket_number, "LX-CNZ 10001")
        self.assertEqual(ticket.status, "mr_submitted")
        self.assertEqual(ticket.assign_name, "Vrunda Rai")
        self.assertTrue(ticket.link_key)

    def test_a_link_twice_in_the_file_merges_into_one_ticket(self):
        """
        The workbook repeats 498 links. Both lines must land on one ticket, and
        the second must diff against what the first set, not against the stored
        value, or its blanks would read as "no change" and its values would be
        lost.
        """
        Ticket.objects.create(
            purpose="HAE", link_url="https://example.com/dup",
            status="mr_submitted",
        )
        path = self._sheet(
            "2026-08-14,HAE,https://example.com/dup,kw,,,29,,,,,",
            "2026-08-14,HAE,https://example.com/dup,kw,,,35,SC - Hetal Sarode,"
            "2026-08-20,,,",
        )
        first, second = plan(read_sheet(path)[0])
        self.assertEqual(first["verdict"], VERDICT_UPDATE)
        self.assertEqual(second["repeat_of"], first["file_row"])
        # The pair disagrees on DM Comments, which is reported, not hidden.
        self.assertEqual(second["conflicts"], ["dm_comments"])

        call_command("import_dmd_sheet", path, "--commit",
                     stdout=io.StringIO())
        self.assertEqual(Ticket.objects.count(), 1)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.dm_comments, "35")
        self.assertEqual(ticket.assign_name, "Hetal Sarode")
