"""
book_delegate/tests_bulk_update_wiring.py
──────────────────────────────────────────
Phase 3: BookDelegateViewSet's mass-update configuration.

Two concerns:
  1. The excluded fields really are unreachable (they fail loudly with 400
     rather than quietly doing nothing).
  2. The real workflow — 3 delegates to Paid, 2 to Cancelled, on one shared
     invoice — behaves as specified.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import ActionLog
from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from teams.models import Team

User = get_user_model()

BULK = BookDelegateViewSet.as_view({"post": "bulk_update"})
SCHEMA = BookDelegateViewSet.as_view({"get": "bulk_update_schema"})


class _BaseBulk(TestCase):
    @classmethod
    def setUpTestData(cls):
        # crm_permission("bookings") resolves through the team, so a bare
        # superuser is not enough — it returns 403 without one.
        cls.role = Team.objects.create(
            name="phase3_admin_role", is_all_access=True,
        )
        cls.user = User.objects.create_superuser(
            username="phase3_admin", email="p3@example.com", password="x",
        )
        cls.user.role = "admin"
        cls.user.team = cls.role
        cls.user.save()

    def _post(self, body):
        req = self.factory.post("/bulk_update/", body, format="json")
        force_authenticate(req, user=self.user)
        return BULK(req)

    def _preview(self, ids, field, value=None):
        body = {"ids": ids, "field": field, "commit": False}
        if value is not None:
            body["value"] = value
        return self._post(body)

    def _commit(self, ids, field, value):
        plan = self._preview(ids, field, value)
        self.assertEqual(plan.status_code, 200, plan.data)
        return self._post({
            "ids": ids, "field": field, "value": value,
            "commit": True, "plan_hash": plan.data["plan_hash"],
        })


class ExcludedFieldsTests(_BaseBulk):
    """Every field that must NOT be mass-editable returns 400."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.inv = BookEvent.objects.create(
            invoice_number="EXCL-001", event_code="TST - AA",
        )
        self.d = BookDelegate.objects.create(
            invoice=self.inv, event_code="TST - AA",
            first_name="Ann", email="ann@example.com",
        )

    def _assert_rejected(self, field, value):
        r = self._preview([self.d.id], field, value)
        self.assertEqual(r.status_code, 400, f"{field} was NOT rejected")
        self.assertIn("not bulk-editable", r.data["detail"])

    # read-only @property on BookDelegate (models.py:101-111)
    def test_payment_status_rejected(self):   self._assert_rejected("payment_status", "Paid")
    def test_payment_date_rejected(self):     self._assert_rejected("payment_date", "2026-01-01")
    def test_invoice_number_rejected(self):   self._assert_rejected("invoice_number", "X")

    # PARTIALLY managed by save(): forced to 0 on a Cancelled delegate and
    # restored to 1 on the transition off it. A batch write would stick on some
    # rows and be silently reverted on others, after a preview that promised all
    # of them. It moves as a declared side effect of delegate_payment_status.
    def test_delegate_count_rejected(self):   self._assert_rejected("delegate_count", 0)

    # derived in save()
    def test_event_code_rejected(self):       self._assert_rejected("event_code", "AAA - BB")
    def test_edition_rejected(self):          self._assert_rejected("edition", 2026)

    # provenance — mass-editing corrupts the webhook audit trail
    def test_source_rejected(self):           self._assert_rejected("source", "manual")

    # identity: email is half of unique_together (invoice, email), and a name is
    # not a batch property of anybody
    def test_identity_fields_rejected(self):
        for field in ("email", "first_name", "last_name"):
            with self.subTest(field=field):
                self._assert_rejected(field, "x@example.com")

    def test_the_schema_covers_both_groups_and_no_excluded_column(self):
        """
        Both registries derive from their model now, so what must NOT be there is
        the whole safety argument. The seven original fields are still required
        — they are the ones the Bookings workflow runs on.
        """
        req = self.factory.get("/bulk_update_schema/")
        force_authenticate(req, user=self.user)
        r = SCHEMA(req)
        wired = set(r.data["fields"])

        required = {
            "delegate_payment_status", "delegate_payment_type",
            "delegate_ticket_tier", "delegate_paid_or_free",
            "delegate_payment_date", "attendance", "invoice.currency",
        }
        self.assertTrue(required <= wired, required - wired)

        for forbidden in (
            # read-only @property, or derived / partially derived in save()
            "payment_status", "payment_date", "invoice_number",
            "delegate_count", "event_code", "edition", "delegate_number",
            # identity
            "email", "first_name", "last_name",
            # the FK objects themselves
            "invoice", "company",
            # invoice-side identity, derived columns and intake provenance
            "invoice.invoice_number", "invoice.event_code", "invoice.edition",
            "invoice.event_name", "invoice.source", "invoice.form_name",
            "invoice.form_url", "invoice.paid_free",
            # audit
            "id", "created_at", "updated_at", "import_batch_id",
        ):
            self.assertNotIn(forbidden, wired)

        self.assertEqual(r.data["label"], "delegates")
        self.assertTrue(r.data["parent_enabled"])

    def test_every_parent_key_is_dotted_and_grouped_as_parent(self):
        """
        A parent key must carry the `invoice.` prefix — _read_current follows the
        dotted path — and must be group=parent, which is what makes the modal
        show the blast-radius warning instead of the two-click path.
        """
        req = self.factory.get("/bulk_update_schema/")
        force_authenticate(req, user=self.user)
        for key, cfg in SCHEMA(req).data["fields"].items():
            with self.subTest(field=key):
                self.assertEqual(cfg["group"] == "parent", key.startswith("invoice."))

    def test_a_parent_write_declares_that_saving_the_invoice_re_derives_it(self):
        """
        BookEvent.save() rebuilds event_name from the Events catalogue and
        re-parses edition out of event_code on EVERY save, whatever column was
        actually set. The preview must say so rather than presenting a currency
        change as touching one column.
        """
        r = self._preview([self.d.id], "invoice.currency", "GBP")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(r.data["side_effects"]), 1)
        self.assertIn("event_name", r.data["side_effects"][0])

    def test_a_booking_code_write_declares_the_stale_invoice_column(self):
        r = self._preview([self.d.id], "booking_code", "SPK")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn("invoice", r.data["side_effects"][0])

    def test_choices_match_the_model_enums(self):
        req = self.factory.get("/bulk_update_schema/")
        force_authenticate(req, user=self.user)
        f = SCHEMA(req).data["fields"]
        self.assertEqual(f["delegate_payment_status"]["choices"], list(BookEvent.PaymentStatus.values))
        self.assertEqual(f["delegate_payment_type"]["choices"],   list(BookEvent.PaymentType.values))
        self.assertEqual(f["delegate_ticket_tier"]["choices"],    list(BookEvent.TicketTier.values))
        self.assertEqual(f["delegate_paid_or_free"]["choices"],   list(BookEvent.PaidOrFree.values))
        self.assertEqual(f["attendance"]["choices"],              list(BookDelegate.Attendance.values))
        self.assertEqual(f["invoice.currency"]["choices"],        list(BookEvent.Currency.values))


class RealWorkflowTests(_BaseBulk):
    """3 → Paid, 2 → Cancelled, all on one shared invoice."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.inv = BookEvent.objects.create(
            invoice_number="WLKE25AMS-2796", event_code="WLKE - MP",
            payment_status="Pending", currency="USD",
        )
        self.delegates = [
            BookDelegate.objects.create(
                invoice=self.inv, event_code="WLKE - MP",
                first_name=f"D{i}", email=f"d{i}@example.com",
            )
            for i in range(5)
        ]
        self.paid_ids = [d.id for d in self.delegates[:3]]
        self.canc_ids = [d.id for d in self.delegates[3:]]

    def test_full_workflow(self):
        # (f) preview writes nothing
        pre = self._preview(self.paid_ids, "delegate_payment_status", "Paid")
        self.assertEqual(pre.status_code, 200)
        self.assertEqual(pre.data["updated"], 0)
        for d in self.delegates:
            d.refresh_from_db()
            self.assertIsNone(d.delegate_payment_status)

        logs_before = ActionLog.objects.count()

        r1 = self._commit(self.paid_ids, "delegate_payment_status", "Paid")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.data["updated"], 3)

        r2 = self._commit(self.canc_ids, "delegate_payment_status", "Cancelled")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["updated"], 2)
        self.assertEqual(r2.data["side_effects"], ["also sets delegate_count → 0"])

        for d in self.delegates:
            d.refresh_from_db()

        # (a) delegate_count = 1,1,1,0,0 — proves per-object save() ran
        self.assertEqual([d.delegate_count for d in self.delegates], [1, 1, 1, 0, 0])

        # (b) the parent invoice is untouched
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.payment_status, "Pending")

        # (c) the 3 Paid rows now override instead of inheriting
        for d in self.delegates[:3]:
            self.assertEqual(d.delegate_payment_status, "Paid")
            self.assertEqual(d.payment_status, "Pending")   # @property still reads the invoice

        # (d) exactly two batch ActionLogs, each carrying the FULL id list
        self.assertEqual(ActionLog.objects.count(), logs_before + 2)
        logs = list(ActionLog.objects.order_by("created_at")[logs_before:])
        self.assertEqual(logs[0].action, "Bulk updated delegate_payment_status on 3 delegates")
        self.assertEqual(logs[1].action, "Bulk updated delegate_payment_status on 2 delegates")
        self.assertIn(str(sorted(self.paid_ids)), logs[0].details)
        self.assertIn(str(sorted(self.canc_ids)), logs[1].details)
        self.assertIn("requested=3 permitted=3 changed=3 no-op=0", logs[0].details)

        # (e) idempotent — re-running the same update is all no-op
        again = self._preview(self.paid_ids, "delegate_payment_status", "Paid")
        self.assertEqual(again.data["no_op"], 3)
        self.assertEqual(again.data["distribution"], {"Paid": 3})

    def test_valueless_preview_shows_distribution_before_a_value_is_picked(self):
        self._commit(self.paid_ids, "delegate_payment_status", "Paid")
        r = self._preview([d.id for d in self.delegates], "delegate_payment_status")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["distribution"], {"Paid": 3, None: 2})
        self.assertNotIn("no_op", r.data)

    def test_parent_currency_reports_collateral_on_the_shared_invoice(self):
        r = self._preview(self.paid_ids, "invoice.currency", "GBP")
        self.assertEqual(r.data["collateral"]["count"], 2)   # the 2 unselected
        self.assertEqual(r.data["collateral"]["hidden_count"], 0)
