"""
accounts/tests_bookings_full_table_filters.py
──────────────────────────────────────────────
Every filterable Bookings column reaches the WHOLE table, not the loaded page.

WHAT THIS IS GUARDING
DataTable splits its conditions in two (frontend/src/lib/filterSpec.js
partitionConds): a condition the backend registers travels as `filter_spec` and
is evaluated by Postgres over every row; a condition it does not register is
re-applied in the browser against whichever rows have been fetched. The second
path is not a degraded filter, it is a WRONG one — on ~14,800 delegates it
answers from the fifty rows on screen and the footer counts those, so it reads
as a filter that works.

Seven columns were on that path: Name, Sales Executive, Accounts Contact,
Delegate Number, Discount, Added Time and Modified Time. Three of them hold a
value no column holds — the serializer builds full_name, sales_executive_name
and the discount PERCENT in Python — so registering them meant re-stating those
definitions as SQL in book_delegate/views.py. The tests below exist because
nothing else forces the SQL and the serializer to agree, and a filter that
disagrees with the cell it names is worse than one that admits it only saw a
page.

Each test therefore fixes the answer by ID SET over rows that are deliberately
NOT all on one page-worth of data, and every scenario includes at least one row
that must NOT come back.

    python manage.py test accounts.tests_bookings_full_table_filters
"""
import json
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from teams.models import Team

User = get_user_model()
LIST = BookDelegateViewSet.as_view({"get": "list"})
SCHEMA = BookDelegateViewSet.as_view({"get": "filter_schema"})


class BookingsFullTableFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(name="bft_admin", is_all_access=True)
        cls.user = User.objects.create_user(
            username="bft_probe", password="x", role="admin", email="bft@iq-hub.com",
        )
        cls.user.team = cls.role
        cls.user.save()

        # The sales executive whose DISPLAY name the Sales Executive column shows.
        cls.exec_named = User.objects.create_user(
            username="jsmith", password="x", email="js@iq-hub.com",
            first_name="Jane", last_name="Smith",
        )
        # No first/last name at all: get_sales_executive_name falls back to the
        # username, and so must the SQL.
        cls.exec_nameless = User.objects.create_user(
            username="ghost", password="x", email="ghost@iq-hub.com",
        )

    def setUp(self):
        self.factory = APIRequestFactory()

    # ── helpers ──────────────────────────────────────────────────────────────
    def _invoice(self, number, **kwargs):
        return BookEvent.objects.create(
            invoice_number=number, event_code="BF - AA", **kwargs
        )

    def _delegate(self, invoice, first_name, last_name="X", **kwargs):
        # (invoice, email) is unique_together, and several fixtures below put two
        # people with the same FIRST name on one invoice, so the address is
        # derived from both halves of the name rather than the first alone.
        slug = f"{first_name}.{last_name}".strip(".").replace(" ", "").lower() or "x"
        kwargs.setdefault("email", f"{slug}@example.com")
        return BookDelegate.objects.create(
            invoice=invoice, event_code="BF - AA",
            first_name=first_name, last_name=last_name, **kwargs
        )

    def _ids(self, criteria):
        """
        The id set the server returns for this spec.

        page_size is deliberately larger than the fixture: the point of these
        tests is what the DATABASE matched, and a paginated answer would let a
        page-only filter pass by accident.
        """
        spec = json.dumps({"match": "all", "criteria": criteria})
        req = self.factory.get("/?" + urlencode({"filter_spec": spec, "page_size": 500}))
        force_authenticate(req, user=self.user)
        resp = LIST(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        return {r["id"] for r in json.loads(resp.content)["results"]}

    def _schema(self):
        req = self.factory.get("/")
        force_authenticate(req, user=self.user)
        resp = SCHEMA(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        return json.loads(resp.content)

    # ── registration ─────────────────────────────────────────────────────────
    def test_every_bookings_column_is_registered(self):
        """
        The seven columns that used to filter in the browser are filterable, and
        the schema says so — which is what frontend/src/lib/filterSpec.js reads
        before it will send a criterion at all. A field missing here silently
        returns the column to page-only filtering.
        """
        fields = self._schema()["fields"]
        for key in ("name", "owner", "accounts_contact_email", "delegate_number",
                    "discount_percent", "added_time", "modified_time"):
            self.assertIn(key, fields, f"'{key}' is not filterable — the Bookings "
                                       f"column would fall back to the loaded page")

    def test_datetime_columns_declare_has_time(self):
        """
        Added/Modified Time are DateTimeFields. Without has_time the client sends
        a bare date as the upper bound, which is that day's MIDNIGHT, and the day
        the user asked for is dropped — accounts/period_filter.day_bounds().
        """
        fields = self._schema()["fields"]
        self.assertTrue(fields["added_time"]["has_time"])
        self.assertTrue(fields["modified_time"]["has_time"])

    # ── name ─────────────────────────────────────────────────────────────────
    def test_name_filters_on_the_full_name_the_cell_shows(self):
        inv = self._invoice("BF-1")
        smith = self._delegate(inv, "Jane", "Smith")
        jones = self._delegate(inv, "Jane", "Jones")
        other = self._delegate(inv, "Bob", "Smithers")

        # "jane smith" spans both columns: no single-column filter can express
        # it, which is the whole reason the annotation exists.
        self.assertEqual(
            self._ids([{"field": "name", "op": "contains", "value": "jane smith"}]),
            {smith.id},
        )
        self.assertEqual(
            self._ids([{"field": "name", "op": "is", "value": "Jane Smith"}]),
            {smith.id},
        )
        self.assertEqual(
            self._ids([{"field": "name", "op": "contains", "value": "smith"}]),
            {smith.id, other.id},
        )
        self.assertNotIn(
            jones.id,
            self._ids([{"field": "name", "op": "contains", "value": "smith"}]),
        )

    def test_name_is_trimmed_like_full_name(self):
        """
        BookDelegate.full_name is `"first last".strip()`, so a delegate with no
        surname displays as "Cher" and must answer to `is "Cher"`. The ordering
        annotation _sort_name is NOT trimmed — it holds "Cher " — which is why
        this filter does not reuse it.
        """
        inv = self._invoice("BF-2")
        cher = self._delegate(inv, "Cher", "")
        self.assertEqual(
            self._ids([{"field": "name", "op": "is", "value": "Cher"}]),
            {cher.id},
        )

    # ── sales executive ──────────────────────────────────────────────────────
    def test_owner_filters_on_the_display_name_with_username_fallback(self):
        named = self._invoice("BF-3", sales_executive=self.exec_named)
        nameless = self._invoice("BF-4", sales_executive=self.exec_nameless)
        unassigned = self._invoice("BF-5")
        d_named = self._delegate(named, "A")
        d_nameless = self._delegate(nameless, "B")
        d_none = self._delegate(unassigned, "C")

        self.assertEqual(
            self._ids([{"field": "owner", "op": "is", "value": "Jane Smith"}]),
            {d_named.id},
        )
        # get_sales_executive_name returns the username when there is no full
        # name; the cell shows "ghost", so "ghost" must match.
        self.assertEqual(
            self._ids([{"field": "owner", "op": "is", "value": "ghost"}]),
            {d_nameless.id},
        )
        # No sales executive at all is empty, not the string "None".
        self.assertEqual(
            self._ids([{"field": "owner", "op": "is_empty"}]),
            {d_none.id},
        )

    # ── accounts contact ─────────────────────────────────────────────────────
    def test_accounts_contact_falls_back_to_the_delegates_own_email(self):
        """
        Mirrors serializers.get_accounts_contact_email: the invoice's address,
        else the delegate's own. Filtering the invoice column alone would miss
        every inheriting row — which is most of them.
        """
        with_contact = self._invoice("BF-6", accounts_contact_email="ap@acme.com")
        without = self._invoice("BF-7")
        d_own = self._delegate(with_contact, "D")
        d_inherit = self._delegate(without, "Erin", email="erin@example.com")

        self.assertEqual(
            self._ids([{"field": "accounts_contact_email", "op": "is",
                        "value": "ap@acme.com"}]),
            {d_own.id},
        )
        self.assertEqual(
            self._ids([{"field": "accounts_contact_email", "op": "is",
                        "value": "erin@example.com"}]),
            {d_inherit.id},
        )

    # ── delegate number ──────────────────────────────────────────────────────
    def test_delegate_number_filters_including_the_multi_value_form(self):
        """
        Delegate Number is 0 or 1, so the natural filter names BOTH values, and a
        multi-value "Is" maps onto `any_of`. That operator was not offered on
        numeric fields, so this exact filter — the common one — had no backend
        form and fell back to the page.
        """
        inv = self._invoice("BF-8")
        zero = self._delegate(inv, "Zero", delegate_number=0)
        one = self._delegate(inv, "One", delegate_number=1)
        two = self._delegate(inv, "Two", delegate_number=2)

        self.assertEqual(
            self._ids([{"field": "delegate_number", "op": "is", "value": 1}]),
            {one.id},
        )
        self.assertEqual(
            self._ids([{"field": "delegate_number", "op": "any_of", "values": [0, 1]}]),
            {zero.id, one.id},
        )
        self.assertEqual(
            self._ids([{"field": "delegate_number", "op": "none_of", "values": [0, 1]}]),
            {two.id},
        )

    # ── discount ─────────────────────────────────────────────────────────────
    def test_discount_is_filtered_in_percent_not_in_the_stored_fraction(self):
        """
        The cell shows 20; the column holds 0.20. `discount_percent` annotates
        discount * 100 so the criterion is written in the units the user can see
        — filtering the raw column for 20 would match nothing while looking like
        it worked.
        """
        inv = self._invoice("BF-9")
        d20 = self._delegate(inv, "Twenty", discount="0.20")
        d50 = self._delegate(inv, "Fifty", discount="0.50")
        d0 = self._delegate(inv, "Nil", discount="0")

        self.assertEqual(
            self._ids([{"field": "discount_percent", "op": "is", "value": 20}]),
            {d20.id},
        )
        self.assertEqual(
            self._ids([{"field": "discount_percent", "op": "gt", "value": 0}]),
            {d20.id, d50.id},
        )
        self.assertNotIn(
            d0.id,
            self._ids([{"field": "discount_percent", "op": "gte", "value": 20}]),
        )
        # The raw column is still registered, in its own units, for any caller
        # that means the fraction.
        self.assertEqual(
            self._ids([{"field": "discount", "op": "is", "value": "0.20"}]),
            {d20.id},
        )

    def test_discount_percent_survives_two_criteria_of_different_shapes(self):
        """
        `contains` compares a number as TEXT and needs a Cast; `gt` in the same
        request must stay numeric. Both used to be annotated under one name, so
        the second silently replaced the first.
        """
        inv = self._invoice("BF-10")
        d20 = self._delegate(inv, "Twenty", discount="0.20")
        self._delegate(inv, "Two", discount="0.02")

        self.assertEqual(
            self._ids([
                {"field": "discount_percent", "op": "contains", "value": "20"},
                {"field": "discount_percent", "op": "gt", "value": 1},
            ]),
            {d20.id},
        )

    # ── added / modified time ────────────────────────────────────────────────
    def test_added_time_filters_the_whole_day_not_its_midnight(self):
        from datetime import datetime, timezone as dt_timezone

        inv = self._invoice("BF-11")
        early = self._delegate(inv, "Early")
        late = self._delegate(inv, "Late")
        outside = self._delegate(inv, "Outside")
        BookDelegate.objects.filter(pk=early.pk).update(
            created_at=datetime(2026, 3, 4, 0, 30, tzinfo=dt_timezone.utc))
        BookDelegate.objects.filter(pk=late.pk).update(
            created_at=datetime(2026, 3, 4, 23, 45, tzinfo=dt_timezone.utc))
        BookDelegate.objects.filter(pk=outside.pk).update(
            created_at=datetime(2026, 3, 5, 0, 5, tzinfo=dt_timezone.utc))

        # The two edges of the day, which is what the client sends for a picked
        # date on a has_time field.
        self.assertEqual(
            self._ids([{"field": "added_time", "op": "between", "values": [
                "2026-03-04T00:00:00+00:00", "2026-03-04T23:59:59.999999+00:00"]}]),
            {early.id, late.id},
        )

    def test_modified_time_is_filterable(self):
        from datetime import datetime, timezone as dt_timezone

        inv = self._invoice("BF-12")
        touched = self._delegate(inv, "Touched")
        stale = self._delegate(inv, "Stale")
        BookDelegate.objects.filter(pk=touched.pk).update(
            updated_at=datetime(2026, 7, 1, 12, 0, tzinfo=dt_timezone.utc))
        BookDelegate.objects.filter(pk=stale.pk).update(
            updated_at=datetime(2026, 1, 1, 12, 0, tzinfo=dt_timezone.utc))

        self.assertEqual(
            self._ids([{"field": "modified_time", "op": "after",
                        "value": "2026-06-30T23:59:59.999999+00:00"}]),
            {touched.id},
        )

    # ── like ─────────────────────────────────────────────────────────────────
    def test_like_patterns_are_evaluated_by_the_database(self):
        """
        "Is Like" is offered by the table's operator list. It had no backend
        form, so choosing it filtered the loaded page alone. % is any run of
        characters and _ is exactly one, matching DataTable's likeTest.
        """
        inv = self._invoice("BF-13")
        smith = self._delegate(inv, "Jane", "Smith")
        smyth = self._delegate(inv, "Jane", "Smyth")
        smithers = self._delegate(inv, "Jane", "Smithers")

        self.assertEqual(
            self._ids([{"field": "name", "op": "like", "value": "Jane Sm_th"}]),
            {smith.id, smyth.id},
        )
        self.assertEqual(
            self._ids([{"field": "name", "op": "like", "value": "%smith%"}]),
            {smith.id, smithers.id},
        )
        # Anchored: a pattern with no wildcards is an exact, case-insensitive match.
        self.assertEqual(
            self._ids([{"field": "name", "op": "like", "value": "jane smith"}]),
            {smith.id},
        )

    def test_like_treats_regex_metacharacters_as_literals(self):
        """
        The pattern is translated to a regex, so an unescaped '.' or '+' in a
        user's value would match more than they typed.
        """
        inv = self._invoice("BF-14")
        dotted = self._delegate(inv, "A.B", "Co")
        undotted = self._delegate(inv, "AXB", "Co")

        self.assertEqual(
            self._ids([{"field": "name", "op": "like", "value": "A.B Co"}]),
            {dotted.id},
        )
        self.assertNotIn(
            undotted.id,
            self._ids([{"field": "name", "op": "like", "value": "A.B Co"}]),
        )
