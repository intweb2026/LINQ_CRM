"""
ticket_central/tests_entry_grid.py
───────────────────────────────────
The inline entry grid's three endpoints, plus the two rules that came with it.

What is worth pinning here, and why:

  · The repeated-link rule has three outcomes, not two, and the difference is
    the whole point. Same link under the same purpose inside the block window
    refuses the save; the same link under a DIFFERENT purpose only warns,
    because repeating a directory page across purposes is normal practice in
    this data. Collapse those and the feature is either useless or unusable.
  · bulk_create must preserve ENTRY ORDER. Added Time is the table's sort key
    now, so a batch whose timestamps do not ascend down the grid reorders
    itself the moment it is saved.
  · The list is scoped by author. A ticket somebody else raised must not appear,
    and data_mining must still see everything or the handover breaks.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from .models import Ticket
from .serializers import TicketCreateSerializer
from .tests import auth, make_ticket, make_user
from .utils import DUP_BLOCK_DAYS, link_digest, normalize_link

LINK = "https://www.example.com/directory/page"


class LinkNormalisationTests(APITestCase):

    def test_noise_that_does_not_change_the_page_is_ignored(self):
        base = normalize_link("https://www.Example.com/Directory/")
        for variant in (
            "http://example.com/Directory",
            "https://example.com/Directory/",
            "  https://WWW.example.com/Directory//  ",
            "//www.example.com/Directory",
        ):
            self.assertEqual(normalize_link(variant), base, variant)

    def test_a_different_page_stays_different(self):
        a = normalize_link("https://example.com/list?page=1")
        b = normalize_link("https://example.com/list?page=2")
        self.assertNotEqual(a, b)

    def test_blank_link_has_no_digest(self):
        self.assertEqual(link_digest(""), "")
        self.assertEqual(link_digest(None), "")

    def test_digest_is_written_on_save_and_kept_in_step(self):
        t = make_ticket(link_url=LINK)
        self.assertEqual(t.link_key, link_digest(LINK))
        t.link_url = "https://other.example.com/x"
        t.save()
        t.refresh_from_db()
        self.assertEqual(t.link_key, link_digest("https://other.example.com/x"))


class CheckLinksTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.mr = make_user("grid_mr", "market_research")

    def _check(self, rows):
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/check_links/",
                                {"rows": rows}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.data["results"]

    def test_unseen_link_is_clean(self):
        result = self._check([{"link_url": LINK, "purpose": "SCU"}])[0]
        self.assertIsNone(result["severity"])
        self.assertEqual(result["matches"], [])

    def test_same_purpose_inside_the_window_blocks(self):
        make_ticket(link_url=LINK, purpose="SCU", ticket_number="WH-SCU 10001")
        result = self._check([{"link_url": LINK, "purpose": "SCU"}])[0]
        self.assertEqual(result["severity"], "block")
        self.assertEqual(result["matches"][0]["ticket_number"], "WH-SCU 10001")
        self.assertTrue(result["matches"][0]["same_purpose"])

    def test_same_purpose_older_than_the_window_only_warns(self):
        old = make_ticket(link_url=LINK, purpose="SCU", ticket_number="WH-SCU 10001")
        Ticket.objects.filter(id=old.id).update(
            created_at=timezone.now() - timedelta(days=DUP_BLOCK_DAYS + 5))
        result = self._check([{"link_url": LINK, "purpose": "SCU"}])[0]
        self.assertEqual(result["severity"], "warn")
        self.assertFalse(result["matches"][0]["within_window"])

    def test_different_purpose_only_warns_and_names_the_earlier_purpose(self):
        make_ticket(link_url=LINK, purpose="BAPE", ticket_number="WH-BAPE 10001")
        result = self._check([{"link_url": LINK, "purpose": "SCU"}])[0]
        self.assertEqual(result["severity"], "warn")
        self.assertEqual(result["matches"][0]["purpose"], "BAPE")
        self.assertFalse(result["matches"][0]["same_purpose"])

    def test_the_same_purpose_match_is_reported_first(self):
        """
        A link can carry many earlier tickets — one page in the live table has
        nine across eight purposes. The message names matches[0], so the
        same-purpose ticket has to lead even when it is not the newest.
        """
        same = make_ticket(link_url=LINK, purpose="SCU", ticket_number="WH-SCU 10001")
        Ticket.objects.filter(id=same.id).update(
            created_at=timezone.now() - timedelta(days=10))
        make_ticket(link_url=LINK, purpose="BAPE", ticket_number="WH-BAPE 10009")

        result = self._check([{"link_url": LINK, "purpose": "SCU"}])[0]
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["matches"][0]["ticket_number"], "WH-SCU 10001")
        self.assertTrue(result["matches"][0]["same_purpose"])
        self.assertEqual(result["severity"], "block")

    def test_purpose_comparison_ignores_case_and_padding(self):
        make_ticket(link_url=LINK, purpose="scu ", ticket_number="WH-SCU 10001")
        result = self._check([{"link_url": LINK, "purpose": " SCU"}])[0]
        self.assertEqual(result["severity"], "block")

    def test_one_query_answers_the_whole_batch(self):
        make_ticket(link_url=LINK, purpose="SCU")
        rows = [{"link_url": LINK, "purpose": "SCU"} for _ in range(25)]
        auth(self.client, self.mr)
        # Two: the token/user lookup, then ONE link_key lookup covering all 25
        # rows. Pinned exactly, because the obvious implementation of this
        # endpoint is a query per row and that is what it must not become.
        with self.assertNumQueries(2):
            resp = self.client.post("/api/tickets/check_links/",
                                    {"rows": rows}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(all(r["severity"] == "block"
                            for r in resp.data["results"]))

    def test_a_repeat_is_found_even_when_someone_else_raised_it(self):
        """
        The check is deliberately unscoped. A duplicate is a duplicate whoever
        typed it, and hiding a colleague's ticket here would wave through the
        exact clash the check exists to catch.
        """
        other = make_user("grid_other_mr", "market_research")
        make_ticket(link_url=LINK, purpose="SCU", created_by=other,
                    ticket_number="WH-SCU 10001")
        result = self._check([{"link_url": LINK, "purpose": "SCU"}])[0]
        self.assertEqual(result["severity"], "block")

    def test_malformed_body_is_a_clean_400(self):
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/check_links/",
                                {"rows": "nope"}, format="json")
        self.assertEqual(resp.status_code, 400)


class BulkCreateTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.mr = make_user("grid_bulk_mr", "market_research")

    def _post(self, rows):
        auth(self.client, self.mr)
        return self.client.post("/api/tickets/bulk_create/",
                                {"rows": rows}, format="json")

    @staticmethod
    def _row(n, purpose="SCU", link=None):
        return {
            "link_url": link or f"https://example.com/company/{n}",
            "purpose": purpose,
            "type_of_ticket": "White - WH",
            "priority": "DD",
            "estimate": 100 + n,
        }

    def test_batch_is_created_in_the_order_given(self):
        resp = self._post([self._row(i) for i in range(5)])
        self.assertEqual(resp.status_code, 201, resp.content)
        created = resp.data["created"]
        self.assertEqual(len(created), 5)

        # Added Time is the table's sort key, so it has to ascend with the grid.
        stamps = [row["created_at"] for row in created]
        self.assertEqual(stamps, sorted(stamps))
        ids = [row["id"] for row in created]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual([row["estimate"] for row in created],
                         [100, 101, 102, 103, 104])

    def test_ticket_numbers_come_from_the_per_purpose_sequence(self):
        resp = self._post([self._row(i) for i in range(3)])
        self.assertEqual(resp.status_code, 201, resp.content)
        numbers = [row["ticket_number"] for row in resp.data["created"]]
        self.assertEqual(numbers, ["WH-SCU 10001", "WH-SCU 10002", "WH-SCU 10003"])

    def test_the_author_is_recorded_on_every_row(self):
        resp = self._post([self._row(i) for i in range(3)])
        self.assertEqual(resp.status_code, 201, resp.content)
        for row in resp.data["created"]:
            self.assertEqual(row["added_user_text"], self.mr.username)
        self.assertEqual(
            Ticket.objects.filter(created_by=self.mr).count(), 3)

    def test_a_blocking_repeat_creates_nothing_and_reports_the_row(self):
        make_ticket(link_url=LINK, purpose="SCU", ticket_number="WH-SCU 10001")
        rows = [self._row(0), self._row(1, link=LINK), self._row(2)]
        resp = self._post(rows)

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("1", resp.data["errors"])
        self.assertIn("link_url", resp.data["errors"]["1"])
        # All or nothing: the two good rows are not half-saved.
        self.assertEqual(Ticket.objects.filter(created_by=self.mr).count(), 0)

    def test_two_identical_rows_in_one_batch_are_caught(self):
        rows = [self._row(0, link=LINK), self._row(1, link=LINK)]
        resp = self._post(rows)

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("1", resp.data["errors"])
        self.assertNotIn("0", resp.data["errors"])
        self.assertIn("row 1", resp.data["errors"]["1"]["link_url"])

    def test_the_same_link_under_a_different_purpose_saves_with_a_warning(self):
        make_ticket(link_url=LINK, purpose="BAPE", ticket_number="WH-BAPE 10001")
        resp = self._post([self._row(0, purpose="SCU", link=LINK)])

        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertIn("0", resp.data["warnings"])
        self.assertEqual(resp.data["warnings"]["0"]["severity"], "warn")

    def test_field_errors_are_reported_per_row(self):
        rows = [self._row(0), {"link_url": "https://example.com/x"}]
        resp = self._post(rows)

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("1", resp.data["errors"])
        self.assertIn("purpose", resp.data["errors"]["1"])

    def test_an_empty_batch_is_a_clean_400(self):
        self.assertEqual(self._post([]).status_code, 400)

    def test_data_mining_may_not_raise_tickets(self):
        dmd = make_user("grid_dmd", "data_mining")
        auth(self.client, dmd)
        resp = self.client.post("/api/tickets/bulk_create/",
                                {"rows": [self._row(0)]}, format="json")
        self.assertEqual(resp.status_code, 403)


class SingleCreateRepeatTests(APITestCase):
    """The rule holds on POST /api/tickets/ too, not only in the grid."""

    @classmethod
    def setUpTestData(cls):
        cls.mr = make_user("grid_single_mr", "market_research")

    def test_blocking_repeat_is_refused_on_the_plain_create_route(self):
        make_ticket(link_url=LINK, purpose="SCU", ticket_number="WH-SCU 10001")
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/", {
            "purpose": "SCU", "type_of_ticket": "White - WH", "link_url": LINK,
        }, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("link_url", resp.data)

    def test_the_check_can_be_waived_for_a_caller_with_no_human(self):
        """
        The block is written for a person: it says change the purpose or work the
        existing ticket, and the grid shows it on the cell so they can. A webhook
        sender has nobody to read that, so webhooks/views.py passes
        skip_link_check and relies on its own idempotency instead. Enforcing it
        there would turn a legitimate delivery into a silent 400 on the far side
        of an integration nobody watches.
        """
        make_ticket(link_url=LINK, purpose="SCU", ticket_number="WH-SCU 10001")
        ser = TicketCreateSerializer(
            data={"purpose": "SCU", "type_of_ticket": "White - WH", "link_url": LINK},
            context={"request": None, "skip_link_check": True},
        )
        self.assertTrue(ser.is_valid(), ser.errors)

    def test_a_ticket_with_no_link_is_never_a_repeat(self):
        make_ticket(link_url="", purpose="SCU")
        auth(self.client, self.mr)
        resp = self.client.post("/api/tickets/", {
            "purpose": "SCU", "type_of_ticket": "White - WH",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)


class AuthorScopingTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.mine = make_user("scope_mine", "market_research")
        cls.other = make_user("scope_other", "market_research")
        cls.dmd = make_user("scope_dmd", "data_mining")
        cls.admin = make_user("scope_admin", "admin")
        cls.t_mine = make_ticket(purpose="MINE", created_by=cls.mine)
        cls.t_other = make_ticket(purpose="OTHER", created_by=cls.other)
        # A migrated row: nobody's FK, a legacy Zoho name in Added User.
        cls.t_legacy = make_ticket(purpose="LEGACY",
                                   added_user_text="zoho_linq-corporate")

    def _ids(self, user):
        auth(self.client, user)
        resp = self.client.get("/api/tickets/")
        self.assertEqual(resp.status_code, 200, resp.content)
        return {row["id"] for row in resp.data["results"]}

    def test_a_scoped_role_sees_only_what_it_added(self):
        self.assertEqual(self._ids(self.mine), {self.t_mine.id})

    def test_added_user_text_also_grants_sight_of_a_row(self):
        """An import that names a real person hands them their own rows."""
        named = make_ticket(purpose="NAMED", added_user_text=self.mine.username)
        self.assertEqual(self._ids(self.mine), {self.t_mine.id, named.id})

    def test_migrated_rows_are_invisible_to_a_scoped_role(self):
        self.assertNotIn(self.t_legacy.id, self._ids(self.mine))

    def test_data_mining_sees_the_whole_queue(self):
        self.assertEqual(
            self._ids(self.dmd),
            {self.t_mine.id, self.t_other.id, self.t_legacy.id},
        )

    def test_admin_sees_everything(self):
        self.assertEqual(
            self._ids(self.admin),
            {self.t_mine.id, self.t_other.id, self.t_legacy.id},
        )

    def test_someone_elses_ticket_is_not_reachable_by_id(self):
        auth(self.client, self.mine)
        resp = self.client.get(f"/api/tickets/{self.t_other.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_the_purpose_picker_is_not_scoped(self):
        """
        A new MR user has no tickets. Scoping the picker would leave them with
        an empty dropdown and no way to type the code the team already uses.
        """
        auth(self.client, self.mine)
        resp = self.client.get("/api/tickets/purposes/")
        self.assertEqual(resp.status_code, 200, resp.content)
        codes = {row["purpose"] for row in resp.data}
        self.assertIn("OTHER", codes)
        self.assertIn("LEGACY", codes)


class OrderingTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.mr = make_user("order_mr", "market_research")

    def test_the_list_is_oldest_first_so_new_entries_land_at_the_end(self):
        first = make_ticket(purpose="FIRST", created_by=self.mr)
        second = make_ticket(purpose="SECOND", created_by=self.mr)
        third = make_ticket(purpose="THIRD", created_by=self.mr)

        auth(self.client, self.mr)
        resp = self.client.get("/api/tickets/")
        self.assertEqual(resp.status_code, 200, resp.content)
        ids = [row["id"] for row in resp.data["results"]]
        self.assertEqual(ids, [first.id, second.id, third.id])
