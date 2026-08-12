"""
accounts/tests_resolved_ordering.py
────────────────────────────────────
Ordering a resolved (person-level) column must order by the RESOLVED value.

THE BUG
BookingsPage sorts Payment Status with `ordering=_sort_status`, and
BookDelegateViewSet annotates

    _sort_status = F("invoice__payment_status")

— the INVOICE column, not

    COALESCE(NULLIF(delegate_payment_status, ''), invoice.payment_status)

which is what the cell displays (`effective_payment_status`). So the moment a
delegate carries an override, the header claims one order and the rows are in
another. The two agree only while no override exists, which was true of the live
database and is why this could not be reproduced from real data.

WHY A FIXTURE
Round 2 declined to fix this citing "cannot reproduce from live data". That was
the wrong conclusion: the absence of a triggering row is a property of one
dataset, not evidence the code is correct. These rows are constructed so the raw
and resolved values disagree, which is the only condition under which the two
orderings differ at all.

test_..._is_wrong_when_ordering_by_the_invoice_column documents the broken
behaviour explicitly, so if someone points serverOrdering back at `_sort_status`
the suite says why that is wrong rather than just going red.

Rolled back per test; the real `linq_crm` is never touched.
"""
import json
from datetime import date
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import CustomRole
from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent

User = get_user_model()
LIST = BookDelegateViewSet.as_view({"get": "list"})


class ResolvedOrderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.role = CustomRole.objects.create(
            name="ro_admin", display_label="RO", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="ro_probe", password="x", role="admin", email="ro@iq-hub.com",
        )
        cls.user.custom_role = cls.role
        cls.user.save()

    def setUp(self):
        self.factory = APIRequestFactory()
        # Invoice value and override are deliberately INVERTED against each other,
        # so ordering by the invoice column produces the exact opposite order to
        # ordering by the resolved value.
        #
        #   row   invoice.payment_status   override      resolved
        #   A     "Unpaid"                 "Cancelled"   "Cancelled"
        #   B     "Cancelled"              "Unpaid"      "Unpaid"
        #
        # ascending by resolved  -> A ("Cancelled"), then B ("Unpaid")
        # ascending by invoice   -> B ("Cancelled"), then A ("Unpaid")   <- wrong
        inv_a = BookEvent.objects.create(
            invoice_number="RO-A", event_code="RO - AA",
            payment_status="Unpaid", request_date=date(2026, 1, 1),
        )
        inv_b = BookEvent.objects.create(
            invoice_number="RO-B", event_code="RO - AA",
            payment_status="Cancelled", request_date=date(2026, 1, 2),
        )
        self.a = BookDelegate.objects.create(
            invoice=inv_a, event_code="RO - AA", first_name="A", last_name="Row",
            email="a@example.com", delegate_payment_status="Cancelled",
        )
        self.b = BookDelegate.objects.create(
            invoice=inv_b, event_code="RO - AA", first_name="B", last_name="Row",
            email="b@example.com", delegate_payment_status="Unpaid",
        )

    def _ids_in_order(self, ordering):
        req = self.factory.get("/?" + urlencode({"ordering": ordering, "page_size": 50}))
        force_authenticate(req, user=self.user)
        resp = LIST(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        body = json.loads(resp.content)
        return [r["id"] for r in body["results"]], body["results"]

    def test_ordering_by_the_invoice_column_is_wrong(self):
        """
        Reproduces the defect. Ordering by `_sort_status` sorts on
        invoice.payment_status, so the rows come back in the opposite order to the
        `effective_payment_status` values the table displays.
        """
        ids, rows = self._ids_in_order("_sort_status")
        shown = [r["effective_payment_status"] for r in rows]

        self.assertEqual(
            ids, [self.b.id, self.a.id],
            "expected the INVOICE-column order (B then A) — if this changed, "
            "_sort_status is no longer annotating invoice__payment_status",
        )
        # The displayed values are therefore NOT ascending: "Unpaid" before "Cancelled".
        self.assertEqual(shown, ["Unpaid", "Cancelled"])
        self.assertNotEqual(
            shown, sorted(shown),
            "the whole point: ordering by the invoice column leaves the DISPLAYED "
            "values unsorted",
        )

    def test_ordering_by_the_resolved_annotation_is_correct(self):
        """The fix: `_sort_effective_payment_status` orders by what is displayed."""
        ids, rows = self._ids_in_order("_sort_effective_payment_status")
        shown = [r["effective_payment_status"] for r in rows]

        self.assertEqual(ids, [self.a.id, self.b.id])
        self.assertEqual(shown, sorted(shown), f"displayed values not ascending: {shown}")

    def test_resolved_ordering_reverses(self):
        ids, rows = self._ids_in_order("-_sort_effective_payment_status")
        shown = [r["effective_payment_status"] for r in rows]

        self.assertEqual(ids, [self.b.id, self.a.id])
        self.assertEqual(shown, sorted(shown, reverse=True))

    def test_blank_override_inherits_for_ordering_too(self):
        """
        NULLIF: an override of '' must order by the INVOICE value, matching what
        effective_payment_status displays for that row.
        """
        inv_c = BookEvent.objects.create(
            invoice_number="RO-C", event_code="RO - AA", payment_status="Paid",
        )
        c = BookDelegate.objects.create(
            invoice=inv_c, event_code="RO - AA", first_name="C", last_name="Row",
            email="c@example.com", delegate_payment_status="",
        )
        ids, rows = self._ids_in_order("_sort_effective_payment_status")
        shown = {r["id"]: r["effective_payment_status"] for r in rows}

        self.assertEqual(shown[c.id], "Paid")
        # Cancelled < Paid < Unpaid alphabetically.
        self.assertEqual(ids, [self.a.id, c.id, self.b.id])

    def test_every_resolved_field_has_a_resolved_ordering_term(self):
        """
        All four fields routed through _effective_filter should be orderable by
        their resolved value, not just payment_status — otherwise the next column
        someone makes sortable reintroduces the same defect.
        """
        for term in (
            "_sort_effective_payment_status",
            "_sort_effective_payment_type",
            "_sort_effective_paid_or_free",
            "_sort_effective_ticket_tier",
        ):
            with self.subTest(term=term):
                self.assertIn(
                    term, BookDelegateViewSet.ordering_fields,
                    f"{term} is not in ordering_fields, so DRF will silently DROP it "
                    f"and the rows come back in default order",
                )
                ids, _ = self._ids_in_order(term)
                self.assertEqual(len(ids), 2, "ordering term was rejected or dropped")
