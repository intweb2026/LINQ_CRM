"""
accounts/tests_resolved_fields.py
──────────────────────────────────
Agreement between the two ways this codebase filters a person-level field, over
every branch of the COALESCE fallback.

WHY THE PREVIOUS CHECK PROVED NOTHING
An earlier smoke test compared `?filter_spec={payment_status is Paid}` against
`?payment_status=Paid` on the live database and found both returned 8,489. But
NO delegate in that database carries an override — `delegate_payment_status` is
NULL on all 13,269 rows — so

    COALESCE(NULLIF(delegate_payment_status, ''), invoice.payment_status)

reduced to `invoice.payment_status` for every single row. The comparison
exercised the trivial case and said nothing about the fallback logic, which is
the entire reason book_delegate/filters.py `_effective_filter` exists.

The two implementations are genuinely separate code paths:

    filter_spec  accounts/filter_spec.py — a COALESCE/NULLIF annotation, then
                 the operator runs against the annotation
    FilterSet    book_delegate/filters.py — three ORed Q objects per value:
                 override=v, OR (override IS NULL AND invoice=v),
                 OR (override='' AND invoice=v)

Nothing forces them to agree. These tests do.

COMPARED BY ID SET, NOT BY COUNT
Equal counts with different members is a silent correctness failure. Every
assertion here compares the actual set of returned primary keys.

No data survives: TestCase wraps each test in a transaction that is rolled back,
and Django runs the suite against a separate test database, so the real
`linq_crm` is never touched.

    python manage.py test accounts.tests_resolved_fields
"""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import CustomRole
from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent

User = get_user_model()
LIST = BookDelegateViewSet.as_view({"get": "list"})

# (spec field name, delegate override column, invoice column, two distinct values)
# All four fields route through _effective_filter in book_delegate/filters.py.
RESOLVED_FIELDS = [
    ("payment_status", "delegate_payment_status", "payment_status", "Paid", "Cancelled"),
    ("payment_type", "delegate_payment_type", "payment_type", "Stripe", "Bank"),
    ("paid_or_free", "delegate_paid_or_free", "paid_or_free", "Paid", "Free"),
    ("ticket_tier", "delegate_ticket_tier", "ticket_tier", "EB", "Regular"),
]


class ResolvedFieldAgreementTests(TestCase):
    """filter_spec and the legacy FilterSet must select the same rows."""

    @classmethod
    def setUpTestData(cls):
        cls.role = CustomRole.objects.create(
            name="resolved_admin", display_label="Resolved", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="resolved_probe", password="x", role="admin", email="rp@iq-hub.com",
        )
        cls.user.custom_role = cls.role
        cls.user.save()

    def setUp(self):
        self.factory = APIRequestFactory()

    # ── helpers ──────────────────────────────────────────────────────────────
    def _invoice(self, number, **kwargs):
        return BookEvent.objects.create(
            invoice_number=number, event_code="RF - AA", **kwargs
        )

    def _delegate(self, invoice, first_name, **kwargs):
        return BookDelegate.objects.create(
            invoice=invoice, event_code="RF - AA",
            first_name=first_name, last_name="X",
            email=f"{first_name.lower()}@example.com",
            **kwargs
        )

    def _ids(self, query):
        req = self.factory.get(f"/?{query}&page_size=500")
        force_authenticate(req, user=self.user)
        resp = LIST(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        return {r["id"] for r in json.loads(resp.content)["results"]}

    def _spec_ids(self, field, value):
        spec = json.dumps({"match": "all", "criteria": [
            {"field": field, "op": "is", "value": value},
        ]})
        from urllib.parse import urlencode
        return self._ids(urlencode({"filter_spec": spec}))

    def _legacy_ids(self, field, value):
        from urllib.parse import urlencode
        return self._ids(urlencode({field: value}))

    def _assert_agree(self, field, value, expected=None, note=""):
        spec = self._spec_ids(field, value)
        legacy = self._legacy_ids(field, value)
        self.assertEqual(
            spec, legacy,
            f"\n{field}={value!r} {note}\n"
            f"  filter_spec returned : {sorted(spec)}\n"
            f"  FilterSet returned   : {sorted(legacy)}\n"
            f"  only in filter_spec  : {sorted(spec - legacy)}\n"
            f"  only in FilterSet    : {sorted(legacy - spec)}",
        )
        if expected is not None:
            self.assertEqual(
                spec, expected,
                f"\n{field}={value!r} {note}: both agreed but on the WRONG rows\n"
                f"  returned : {sorted(spec)}\n"
                f"  expected : {sorted(expected)}",
            )
        return spec

    # ── the branches ─────────────────────────────────────────────────────────
    def test_every_coalesce_branch_agrees_by_id_set(self):
        """
        One scenario per branch of COALESCE(NULLIF(override, ''), invoice), for
        each of the four resolved fields.
        """
        for field, override_col, invoice_col, v_a, v_b in RESOLVED_FIELDS:
            with self.subTest(field=field):
                BookDelegate.objects.all().delete()
                BookEvent.objects.all().delete()

                # Branch 1: override SET and DIFFERENT from the invoice.
                # The case that matters — the row must answer to its override
                # (v_a) and must NOT answer to the invoice value (v_b).
                inv1 = self._invoice("RF-1", **{invoice_col: v_b})
                d_override = self._delegate(inv1, "Override", **{override_col: v_a})

                # Branch 2: override is EMPTY STRING with a real invoice value.
                # This is the NULLIF branch: '' must inherit, not match ''.
                inv2 = self._invoice("RF-2", **{invoice_col: v_a})
                d_blank = self._delegate(inv2, "Blank", **{override_col: ""})

                # Branch 3: override NULL with a real invoice value — inherits.
                inv3 = self._invoice("RF-3", **{invoice_col: v_a})
                d_null = self._delegate(inv3, "Nul")

                # Branch 4: override SET where the invoice value is empty.
                inv4 = self._invoice("RF-4", **{invoice_col: ""})
                d_only = self._delegate(inv4, "OnlyOverride", **{override_col: v_a})

                # Branch 5: ONE invoice, TWO delegates with DIFFERENT overrides.
                # The split-invoice workflow: intentional, documented behaviour.
                inv5 = self._invoice("RF-5", **{invoice_col: v_b})
                d_split_a = self._delegate(inv5, "SplitA", **{override_col: v_a})
                d_split_b = self._delegate(inv5, "SplitB", **{override_col: v_b})

                # Rows whose RESOLVED value is v_a:
                #   override=v_a (1), '' inheriting v_a (2), NULL inheriting v_a (3),
                #   override=v_a over an empty invoice (4), split override=v_a (5a)
                expect_a = {d_override.id, d_blank.id, d_null.id, d_only.id, d_split_a.id}
                # Resolved value is v_b: only the split sibling that overrides to v_b.
                # inv1/inv5 carry v_b at invoice level but both their delegates
                # override, so neither inherits it.
                expect_b = {d_split_b.id}

                self._assert_agree(field, v_a, expect_a, "(resolves to the override or the inherited value)")
                self._assert_agree(field, v_b, expect_b, "(invoice value must NOT leak through an override)")

    def test_override_shadows_the_invoice_value(self):
        """
        A row whose override differs from its invoice must answer ONLY to the
        override. Filtering the raw override column would also be wrong here —
        it would miss every inheriting row — so this pins the direction.
        """
        inv = self._invoice("RF-SHADOW", payment_status="Cancelled")
        d = self._delegate(inv, "Shadowed", delegate_payment_status="Paid")

        self.assertEqual(self._assert_agree("payment_status", "Paid"), {d.id})
        self.assertEqual(self._assert_agree("payment_status", "Cancelled"), set())

    def test_split_invoice_delegates_are_independently_filterable(self):
        """
        Two delegates on ONE invoice, split Paid vs Cancelled. This is the
        workflow the resolved fields exist for; each must appear under its own
        status and neither under the other's.
        """
        inv = self._invoice("RF-SPLIT", payment_status="Pending")
        paid = self._delegate(inv, "PaidOne", delegate_payment_status="Paid")
        cancelled = self._delegate(inv, "CancelledOne", delegate_payment_status="Cancelled")
        inheriting = self._delegate(inv, "Inheriting")

        self.assertEqual(self._assert_agree("payment_status", "Paid"), {paid.id})
        self.assertEqual(self._assert_agree("payment_status", "Cancelled"), {cancelled.id})
        self.assertEqual(self._assert_agree("payment_status", "Pending"), {inheriting.id})

    def test_any_of_agrees_with_repeated_query_params(self):
        """
        The multi-value path: filter_spec `any_of` vs the FilterSet's
        MultipleChoiceFilter reading repeated bare keys. This is the form
        DataTable emits for a multi-select, and it has its own Q-building code on
        both sides.
        """
        inv = self._invoice("RF-MULTI", payment_status="Pending")
        paid = self._delegate(inv, "P", delegate_payment_status="Paid")
        cancelled = self._delegate(inv, "C", delegate_payment_status="Cancelled")
        self._delegate(inv, "I")   # inherits Pending — must be excluded

        spec = json.dumps({"match": "all", "criteria": [
            {"field": "payment_status", "op": "any_of", "values": ["Paid", "Cancelled"]},
        ]})
        from urllib.parse import urlencode
        spec_ids = self._ids(urlencode({"filter_spec": spec}))
        # Repeated bare keys — what serializeParams emits for an array.
        legacy_ids = self._ids("payment_status=Paid&payment_status=Cancelled")

        self.assertEqual(
            spec_ids, legacy_ids,
            f"any_of disagreed with repeated params\n"
            f"  filter_spec: {sorted(spec_ids)}\n  FilterSet: {sorted(legacy_ids)}",
        )
        self.assertEqual(spec_ids, {paid.id, cancelled.id})

    def test_is_empty_matches_neither_side_set(self):
        """
        is_empty on a resolved field means "override unset AND invoice unset".
        The FilterSet has no equivalent operator, so this asserts the semantic
        directly rather than by agreement.
        """
        both_empty_inv = self._invoice("RF-E1", payment_status="")
        both_empty = self._delegate(both_empty_inv, "BothEmpty")

        inv_only = self._invoice("RF-E2", payment_status="Paid")
        self._delegate(inv_only, "InheritsPaid")

        override_only_inv = self._invoice("RF-E3", payment_status="")
        self._delegate(override_only_inv, "HasOverride", delegate_payment_status="Paid")

        from urllib.parse import urlencode
        spec = json.dumps({"match": "all", "criteria": [
            {"field": "payment_status", "op": "is_empty"},
        ]})
        self.assertEqual(self._ids(urlencode({"filter_spec": spec})), {both_empty.id})
