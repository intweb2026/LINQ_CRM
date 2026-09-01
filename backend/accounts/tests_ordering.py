"""
accounts/tests_ordering.py
───────────────────────────
Stable pagination.

The bug: every list endpoint sorted by a non-unique column, so tied rows came
back in arbitrary order per query and LIMIT/OFFSET pagination both duplicated
and SKIPPED rows. Measured on the real database before the fix, Bookings pages
1 and 2 shared 10 rows — meaning 10 other rows were never returned at all.

These fixtures deliberately give every row the SAME sort key, which is the
worst case and what the old code could not survive.
"""
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate


from accounts.ordering import StableOrderingFilter
from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from teams.models import Team

User = get_user_model()
LIST = BookDelegateViewSet.as_view({"get": "list"})


class StableOrderingFilterUnitTests(TestCase):
    def setUp(self):
        self.f = StableOrderingFilter()

    def _ordering(self, view_ordering, query=None):
        class _View:
            ordering = view_ordering
            ordering_fields = ["id", "created_at", "email"]
        req = APIRequestFactory().get("/" + (f"?ordering={query}" if query else ""))
        from rest_framework.request import Request
        return self.f.get_ordering(Request(req), BookDelegate.objects.all(), _View())

    def test_pk_appended_to_the_default_ordering(self):
        self.assertEqual(self._ordering(["-created_at"]), ["-created_at", "pk"])

    def test_pk_appended_to_a_user_selected_ordering(self):
        self.assertEqual(self._ordering(["-created_at"], "email"), ["email", "pk"])

    def test_pk_not_duplicated_when_already_sorting_by_id(self):
        self.assertEqual(self._ordering(["-created_at"], "id"), ["id"])
        self.assertEqual(self._ordering(["-created_at"], "-id"), ["-id"])

    def test_pk_added_even_with_no_ordering_at_all(self):
        self.assertEqual(self._ordering(None), ["pk"])


class PaginationStabilityTests(TestCase):
    """Every row shares one sort key — the worst case for a non-unique sort."""

    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(
            name="ord_admin", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="ord_user", password="x", role="admin", email="ord@iq-hub.com",
        )
        cls.user.team = cls.role
        cls.user.save()

        # One invoice AND one shared timestamp, so all 37 rows tie on the sort
        # key. The tie is the whole point of this class: it is what makes
        # LIMIT/OFFSET pagination free to return rows in a different order per
        # query, which is the bug accounts/ordering.py exists to fix. Rows created
        # in a loop each get a distinct microsecond, so without this the class
        # would silently stop testing the tie it is named for.
        #
        # BOTH created_at AND updated_at are tied, because the Bookings default
        # has now been both: it moved -booked_on → -created_at → -updated_at, and
        # whichever column it names next, one of these two is it. created_at is
        # passed to create(); updated_at CANNOT BE, because auto_now=True
        # overwrites any assigned value in pre_save(), so it is forced afterwards
        # with the one write that deliberately bypasses auto_now — a queryset
        # .update(). The single invoice is kept because request_date is still the
        # key behind the Request Date column's own ordering term.
        inv = BookEvent.objects.create(
            invoice_number="ORD-1", event_code="ORD - AA",
            request_date="2026-01-01", payment_status="Pending",
        )
        tied = timezone.make_aware(datetime(2026, 1, 1, 9, 0, 0))
        cls.expected = 37
        for i in range(cls.expected):
            BookDelegate.objects.create(
                invoice=inv, event_code="ORD - AA",
                first_name=f"D{i:02d}", email=f"ord{i:02d}@example.com",
                created_at=tied,
            )
        BookDelegate.objects.filter(invoice=inv).update(updated_at=tied)

    def _sweep(self, page_size, extra=""):
        factory = APIRequestFactory()
        ids, page = [], 1
        while True:
            req = factory.get(f"/?page={page}&page_size={page_size}{extra}")
            force_authenticate(req, user=self.user)
            resp = LIST(req)
            resp.render()
            self.assertEqual(resp.status_code, 200, resp.content)
            body = resp.data
            ids.extend(r["id"] for r in body["results"])
            if not body.get("next"):
                return ids
            page += 1
            self.assertLess(page, 50, "runaway pagination")

    def test_no_duplicates_and_no_omissions_across_pages(self):
        ids = self._sweep(page_size=10)
        self.assertEqual(len(ids), self.expected, "rows were skipped")
        self.assertEqual(len(set(ids)), self.expected, "rows were duplicated")

    def test_holds_at_an_awkward_page_size(self):
        """A page size that does not divide the row count exercises the tail."""
        ids = self._sweep(page_size=7)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(set(ids)), self.expected)

    def test_page_boundaries_are_repeatable(self):
        """Same request twice returns the same page — no arbitrary tie order."""
        first = self._sweep(page_size=10)
        second = self._sweep(page_size=10)
        self.assertEqual(first, second)

    def test_stable_under_a_filter_spec(self):
        import json
        from urllib.parse import quote
        spec = quote(json.dumps({"match": "all", "criteria": [
            {"field": "event_code", "op": "is", "value": "ORD - AA"}]}))
        ids = self._sweep(page_size=10, extra=f"&filter_spec={spec}")
        self.assertEqual(len(ids), self.expected)
        self.assertEqual(len(set(ids)), self.expected)

    def test_explicit_ordering_by_id_is_no_longer_dropped(self):
        ids = self._sweep(page_size=10, extra="&ordering=id")
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), self.expected)


class NullsLastOrderingTests(TestCase):
    """Undated rows sort LAST, in both directions.

    Postgres orders NULLs FIRST on a DESC sort, so "newest first" on a nullable
    date column came back led by every row that had no date at all. The delegate
    date columns are all nullable, and Date Paid is the one users hit first.
    """

    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(name="nulls_admin", is_all_access=True)
        cls.user = User.objects.create_user(
            username="nulls_user", password="x", role="admin",
            email="nulls@iq-hub.com",
        )
        cls.user.team = cls.role
        cls.user.save()

        # Three invoices: two with a payment date, one with none. The dated pair
        # brackets the undated row on both ends of the sort.
        cls.rows = {}
        for name, paid in (("old", "2026-01-01"), ("new", "2026-06-01"), ("none", None)):
            inv = BookEvent.objects.create(
                invoice_number=f"NL-{name}", event_code="NL - AA",
                request_date="2026-01-01", payment_date=paid,
                payment_status="Pending",
            )
            cls.rows[name] = BookDelegate.objects.create(
                invoice=inv, event_code="NL - AA", first_name=name,
                email=f"{name}@example.com",
            )

    def _ids(self, ordering):
        req = APIRequestFactory().get(f"/?page_size=50&ordering={ordering}")
        force_authenticate(req, user=self.user)
        resp = LIST(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        return [r["id"] for r in resp.data["results"]]

    def test_newest_first_puts_the_undated_row_last(self):
        self.assertEqual(
            self._ids("-_sort_effective_payment_date"),
            [self.rows["new"].id, self.rows["old"].id, self.rows["none"].id],
        )

    def test_oldest_first_also_puts_the_undated_row_last(self):
        self.assertEqual(
            self._ids("_sort_effective_payment_date"),
            [self.rows["old"].id, self.rows["new"].id, self.rows["none"].id],
        )

    def test_a_field_outside_the_optin_keeps_its_plain_term(self):
        """created_at is never null and its DESC term must stay index-served."""
        ids = self._ids("-created_at")
        self.assertEqual(len(ids), 3)
