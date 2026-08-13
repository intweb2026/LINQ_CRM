"""
ticket_central/tests_bulk_update.py
────────────────────────────────────
Phase 4: mass update on TicketViewSet.

The invariant that matters here is the three-way status guard. submit_mr,
submit_dmd and return_to_mr each check the current status before transitioning
and stamp provenance; a generic field writer must not be able to route around
them. Several tests below exist purely to prove `status` stays unreachable.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import ActionLog, CustomRole, RolePermission
from ticket_central.models import Ticket
from ticket_central.views import TicketViewSet

User = get_user_model()

BULK       = TicketViewSet.as_view({"post": "bulk_update"})
SCHEMA     = TicketViewSet.as_view({"get": "bulk_update_schema"})
SUBMIT_DMD = TicketViewSet.as_view({"post": "submit_dmd"})
SUBMIT_MR  = TicketViewSet.as_view({"post": "submit_mr"})
RETURN_MR  = TicketViewSet.as_view({"post": "return_to_mr"})


def _role(name, **perms):
    role, _ = CustomRole.objects.get_or_create(
        name=name, defaults={"display_label": name, "is_all_access": False},
    )
    RolePermission.objects.update_or_create(
        custom_role=role, module="ticket_central",
        defaults={"can_view": True, "can_create": False,
                  "can_update": perms.get("update", False), "can_delete": False},
    )
    return role


class TicketBulkUpdateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.all_access = CustomRole.objects.create(
            name="tc_bulk_admin", display_label="TC Bulk Admin", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="tc_bulk_user", password="x", role="admin",
            email="tc.bulk@iq-hub.com",
        )
        cls.user.custom_role = cls.all_access
        cls.user.save()

        # a user who may view but not update
        cls.readonly = User.objects.create_user(
            username="tc_readonly", password="x", role="market_research",
            email="tc.readonly@iq-hub.com",
        )
        cls.readonly.custom_role = _role("tc_view_only", update=False)
        cls.readonly.save()

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tickets = [
            Ticket.objects.create(
                purpose=f"P{i}", type_of_ticket="BX",
                status=Ticket.Status.MR_SUBMITTED, priority="AS",
                relationship="direct",
            )
            for i in range(5)
        ]
        self.ids = [t.id for t in self.tickets]

    def _post(self, body, view=BULK, user=None):
        req = self.factory.post("/bulk_update/", body, format="json")
        force_authenticate(req, user=user or self.user)
        return view(req)

    def _preview(self, ids, field, value=None, user=None):
        body = {"ids": ids, "field": field, "commit": False}
        if value is not None:
            body["value"] = value
        return self._post(body, user=user)

    def _commit(self, ids, field, value, user=None):
        plan = self._preview(ids, field, value, user=user)
        self.assertEqual(plan.status_code, 200, plan.data)
        return self._post({
            "ids": ids, "field": field, "value": value,
            "commit": True, "plan_hash": plan.data["plan_hash"],
        }, user=user)

    # ── (a) the happy path ────────────────────────────────────────────────────
    def test_a_mass_priority_change_leaves_status_alone(self):
        before = [t.status for t in self.tickets]
        r = self._commit(self.ids, "priority", "SPEX")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["updated"], 5)
        for t, was in zip(self.tickets, before):
            t.refresh_from_db()
            self.assertEqual(t.priority, "SPEX")
            self.assertEqual(t.status, was)

    # ── (b) status is unreachable ─────────────────────────────────────────────
    def test_b_status_is_rejected(self):
        r = self._preview(self.ids, "status", "draft")
        self.assertEqual(r.status_code, 400)
        self.assertIn("not bulk-editable", r.data["detail"])
        for t in self.tickets:
            t.refresh_from_db()
            self.assertEqual(t.status, Ticket.Status.MR_SUBMITTED)

    def test_b_excluded_fields_all_rejected(self):
        for field, value in [
            ("status", "draft"),
            ("ticket_number", "XX-1"),
            ("mr_submitted_by", 1), ("mr_submitted_at", "2026-01-01"),
            ("dmd_submitted_by", 1), ("dmd_submitted_at", "2026-01-01"),
            ("returned_by", 1), ("returned_at", "2026-01-01"),
            ("return_reason", "nope"),
        ]:
            r = self._preview(self.ids, field, value)
            self.assertEqual(r.status_code, 400, f"{field} was NOT rejected")

    # ── (c) the submit guards still work afterwards ───────────────────────────
    def test_c_submit_actions_unaffected_by_mass_update(self):
        self._commit(self.ids, "priority", "DD")

        # submit_dmd works on an MR_SUBMITTED ticket
        t = self.tickets[0]
        req = self.factory.post("/submit_dmd/")
        force_authenticate(req, user=self.user)
        resp = SUBMIT_DMD(req, pk=t.pk)
        self.assertEqual(resp.status_code, 200, resp.data)
        t.refresh_from_db()
        self.assertEqual(t.status, Ticket.Status.COMPLETED)
        self.assertEqual(t.dmd_submitted_by, self.user)

        # and is still refused from the wrong status (now COMPLETED)
        req2 = self.factory.post("/submit_dmd/")
        force_authenticate(req2, user=self.user)
        resp2 = SUBMIT_DMD(req2, pk=t.pk)
        self.assertEqual(resp2.status_code, 400)
        self.assertIn("Cannot submit from status", resp2.data["detail"])

    # ── (d) the RETURNED loop survives ────────────────────────────────────────
    def test_d_return_then_resubmit_loop_intact(self):
        self._commit(self.ids, "priority", "AB")
        t = self.tickets[1]

        req = self.factory.post("/return_to_mr/", {"reason": "needs more"}, format="json")
        force_authenticate(req, user=self.user)
        self.assertEqual(RETURN_MR(req, pk=t.pk).status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.status, Ticket.Status.RETURNED)
        self.assertEqual(t.return_reason, "needs more")

        req2 = self.factory.post("/submit_mr/")
        force_authenticate(req2, user=self.user)
        self.assertEqual(SUBMIT_MR(req2, pk=t.pk).status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.status, Ticket.Status.MR_SUBMITTED)

    # ── (e) preview writes nothing ────────────────────────────────────────────
    def test_e_preview_writes_nothing(self):
        r = self._preview(self.ids, "priority", "MEDIA")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["updated"], 0)
        self.assertEqual(r.data["distribution"], {"AS": 5})
        for t in self.tickets:
            t.refresh_from_db()
            self.assertEqual(t.priority, "AS")

    # ── (f) idempotent ────────────────────────────────────────────────────────
    def test_f_rerun_is_all_no_op(self):
        self._commit(self.ids, "priority", "ASSOC")
        again = self._preview(self.ids, "priority", "ASSOC")
        self.assertEqual(again.data["no_op"], 5)

    # ── (g) audit ─────────────────────────────────────────────────────────────
    def test_g_one_actionlog_per_operation_with_full_ids(self):
        before = ActionLog.objects.count()
        self._commit(self.ids, "priority", "AD")
        self.assertEqual(ActionLog.objects.count(), before + 1)
        log = ActionLog.objects.latest("created_at")
        self.assertEqual(log.action, "Bulk updated priority on 5 tickets")
        self.assertIn(str(sorted(self.ids)), log.details)
        self.assertIn("requested=5 permitted=5 changed=5 no-op=0", log.details)

    # ── (h) no parent path => no collateral ───────────────────────────────────
    def test_h_collateral_is_empty(self):
        r = self._preview(self.ids, "priority", "AS")
        self.assertEqual(r.data["collateral"]["count"], 0)
        self.assertEqual(r.data["collateral"]["sample"], [])
        self.assertEqual(r.data["collateral"]["hidden_count"], 0)

    def test_h_schema_declares_no_parent_and_one_group(self):
        req = self.factory.get("/bulk_update_schema/")
        force_authenticate(req, user=self.user)
        r = SCHEMA(req)
        self.assertFalse(r.data["parent_enabled"])
        self.assertEqual(r.data["label"], "tickets")
        groups = {c["group"] for c in r.data["fields"].values()}
        self.assertEqual(groups, {"row"})   # single group => unlabelled list in the UI

    # ── (i) permissions ───────────────────────────────────────────────────────
    def test_i_user_without_can_update_is_forbidden(self):
        r = self._preview(self.ids, "priority", "AS", user=self.readonly)
        self.assertEqual(r.status_code, 403)
        for t in self.tickets:
            t.refresh_from_db()
            self.assertEqual(t.priority, "AS")

    # ── (j) batch assignment ──────────────────────────────────────────────────
    def test_j_batch_assign_to_one_user(self):
        target = self.user.email
        r = self._commit(self.ids, "assigned_mr", target)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["updated"], 5)
        for t in self.tickets:
            t.refresh_from_db()
            self.assertEqual(t.assigned_mr, target)
            self.assertEqual(t.status, Ticket.Status.MR_SUBMITTED)   # untouched

    def test_j_assignee_must_be_an_active_user_email(self):
        r = self._preview(self.ids, "assigned_mr", "typo@nowhere.example")
        self.assertEqual(r.status_code, 400)
        self.assertIn("not a valid choice", r.data["detail"])

    # ── choices sourced from the model enums ──────────────────────────────────
    def test_choices_match_model_enums(self):
        req = self.factory.get("/bulk_update_schema/")
        force_authenticate(req, user=self.user)
        f = SCHEMA(req).data["fields"]
        self.assertEqual(f["priority"]["choices"],       list(Ticket.Priority.values))
        self.assertEqual(f["type_of_ticket"]["choices"], list(Ticket.TypeOfTicket.values))
        self.assertEqual(f["relationship"]["choices"],   list(Ticket.Relationship.values))
        self.assertIn(self.user.email, f["assigned_mr"]["choices"])

    def _schema_fields(self):
        req = self.factory.get("/bulk_update_schema/")
        force_authenticate(req, user=self.user)
        return SCHEMA(req).data["fields"]

    def test_nullable_mirrors_the_model_column(self):
        """
        The text columns are CharField(blank=True, default="") and stay
        non-nullable; the dates and counts are null=True and must be clearable,
        so an MR can wipe a wrongly-entered complete_date across a batch rather
        than only overwrite it. Asserted in both directions against the model.
        """
        columns = {f.name: f for f in Ticket._meta.get_fields()
                   if getattr(f, "concrete", False)}
        fields = self._schema_fields()
        for key, cfg in fields.items():
            if key not in columns:          # assigned_mr is added per request
                continue
            self.assertEqual(
                bool(cfg.get("nullable", False)), bool(columns[key].null),
                f"{key}: nullable disagrees with the model column",
            )
        self.assertTrue(fields["complete_date"]["nullable"])
        self.assertFalse(fields["purpose"].get("nullable", False))

    def test_workflow_state_and_provenance_are_absent_from_the_schema(self):
        """
        test_b_* proves the ENDPOINT refuses these. This proves the SCHEMA never
        advertises them either — the registry derives from the model now, so the
        exclusion list is the whole safety argument, and a field offered in the
        picker but refused on Apply is a bug report waiting to happen.
        """
        wired = set(self._schema_fields())
        for forbidden in ("status", "ticket_number", "external_id",
                          "mr_submitted_at", "mr_submitted_by",
                          "dmd_submitted_at", "dmd_submitted_by",
                          "returned_at", "returned_by", "return_reason",
                          "created_by", "created_at", "updated_at",
                          "idempotency_key", "source_spreadsheet_id",
                          "source_tab", "source_row_number", "id"):
            self.assertNotIn(forbidden, wired)

    def test_a_count_column_takes_a_number_and_refuses_garbage(self):
        bad = self._preview(self.ids, "actual_number", "abc")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("whole number", bad.data["detail"])

        r = self._commit(self.ids, "actual_number", "120")
        self.assertEqual(r.status_code, 200, r.data)
        for t in Ticket.objects.filter(id__in=self.ids):
            self.assertEqual(t.actual_number, 120)

    def test_a_nullable_count_can_be_cleared(self):
        self._commit(self.ids, "mined_count", "50")
        plan = self._post({
            "ids": self.ids, "field": "mined_count", "value": None, "commit": False,
        })
        self.assertEqual(plan.status_code, 200, plan.data)
        r = self._post({
            "ids": self.ids, "field": "mined_count", "value": None,
            "commit": True, "plan_hash": plan.data["plan_hash"],
        })
        self.assertEqual(r.status_code, 200, r.data)
        for t in Ticket.objects.filter(id__in=self.ids):
            self.assertIsNone(t.mined_count)
