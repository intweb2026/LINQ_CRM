"""
credit_control/tests.py
────────────────────────
The routing engine is the load-bearing logic in this module: it decides who
phones whom, and every figure on the dashboard is downstream of it. These tests
pin the five precedence rules, the day-4 handoff and its three exemptions, the
one-way direction of that handoff, idempotency, and the reactivation net.

Nothing here touches HubSpot or Anthropic. The engine makes no network calls, on
purpose, which is exactly why it can be tested like this.
"""
from datetime import timedelta

from django.db.models import Count
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from book_event.models import BookEvent
from events.models import Event
from teams.models import Team

from . import constants, dashboard, engine
from .models import CreditControlDisposition, CreditControlLead


class EngineTestCase(TestCase):
    """Shared fixture: the team, the dispositions, and an invoice factory."""

    @classmethod
    def setUpTestData(cls):
        CreditControlDisposition.seed()

    def setUp(self):
        self.team = Team.objects.create(name="Credit Control")
        self.bruce = self._member("bruce")
        self.derek = self._member("derek")
        self.devin = self._member("devin")
        self.ben = self._member("ben")
        self.team.team_lead = self.bruce
        self.team.save()

    def _member(self, username):
        return User.objects.create_user(
            username=username, email=f"{username}@iq-hub.com", password="x",
            team=self.team,
        )

    def _invoice(self, number, *, code="ISCC", booking="Delegate",
                 days_old=0, paid=None, status=None, verdict=None):
        """
        One pending invoice, `days_old` days after it was raised.

        `verdict` creates the Event row too, because the exclusion rule reads
        the live verdict and there is no point testing it against an event that
        does not exist.
        """
        if verdict is not None:
            Event.objects.get_or_create(
                event_code=code,
                defaults={
                    "name": code,
                    "verdict": verdict,
                    # events.event_date is NOT NULL, so the factory has to
                    # supply one even though nothing here reads it.
                    "event_date": timezone.now().date() + timedelta(days=60),
                },
            )
        return BookEvent.objects.create(
            invoice_number=number,
            event_code=code,
            booking_code=booking,
            invoice_date=timezone.now().date() - timedelta(days=days_old),
            payment_status=status or BookEvent.PaymentStatus.PENDING,
            payment_date=paid,
            company_name=f"Company {number}",
            accounts_contact_email=f"ap-{number}@example.com".lower(),
        )

    def _lead(self, number):
        return CreditControlLead.objects.get(pk=number)


class PrecedenceTests(EngineTestCase):
    """The five rules, in order, and the two orderings that are easy to get wrong."""

    def test_a_plain_pending_invoice_becomes_an_active_lead_for_the_team_lead(self):
        self._invoice("INV-1")
        engine.refresh()
        lead = self._lead("INV-1")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.ACTIVE)
        self.assertEqual(lead.assigned_to, self.bruce)
        self.assertIsNotNone(lead.first_seen)

    def test_an_excluded_verdict_is_not_chased(self):
        for verdict in constants.EXCLUDED_VERDICTS:
            with self.subTest(verdict=verdict):
                CreditControlLead.objects.all().delete()
                BookEvent.objects.all().delete()
                Event.objects.all().delete()
                self._invoice(f"INV-{verdict}", code=f"EV{verdict}", verdict=verdict)
                summary = engine.refresh()
                self.assertEqual(summary["active"], 0)
                self.assertFalse(CreditControlLead.objects.exists())

    def test_a_verdict_that_stays_in_is_chased(self):
        for verdict in ("Standby", "Going Ahead", "Needs a push", "Full Efforts Req."):
            with self.subTest(verdict=verdict):
                CreditControlLead.objects.all().delete()
                BookEvent.objects.all().delete()
                Event.objects.all().delete()
                self._invoice("INV-OK", code="EVOK", verdict=verdict)
                engine.refresh()
                self.assertEqual(
                    self._lead("INV-OK").bucket, CreditControlLead.Bucket.ACTIVE,
                )

    def test_spex_and_its_combinations_leave_the_calling_flow(self):
        """
        The twelve canonical codes that carry SpEx, including the combinations.

        "Speaker / GLD SpEx" answers true to both the SpEx and the speaker test,
        and SpEx has to win or a speaker package would be chased twice, once
        here and once by whoever owns the SpEx tab.
        """
        codes = [
            "GLD SpEx", "PLT SpEx", "PTN SpEx", "SLV SpEx", "Speaker Table",
            "Speaker / GLD SpEx", "Speaker / PLT SpEx", "Speaker / PTN SpEx",
            "Speaker / SLV SpEx", "Upgraded to GLD SpEx", "Upgraded to PLT SpEx",
            "Upgraded to SLV SpEx",
        ]
        for index, code in enumerate(codes):
            self._invoice(f"INV-SPEX-{index}", booking=code)
        summary = engine.refresh()
        self.assertEqual(summary["spex"], len(codes))
        self.assertEqual(summary["active"], 0)
        rows = CreditControlLead.objects.all()
        self.assertEqual(rows.count(), len(codes))
        # Bucketed, but on nobody's queue.
        self.assertEqual(
            rows.filter(bucket=CreditControlLead.Bucket.SPEX,
                        assigned_to__isnull=True).count(),
            len(codes),
        )

    def test_the_ten_remaining_codes_are_chased(self):
        codes = [
            "Delegate", "Add-Ons", "Group Pass", "Media", "Complimentary",
            "Advisory Board Member", "Speaker", "Speaker / Group Pass",
            "SPP", "SPP / Group Pass",
        ]
        for index, code in enumerate(codes):
            self._invoice(f"INV-OK-{index}", booking=code)
        summary = engine.refresh()
        self.assertEqual(summary["active"], len(codes))
        self.assertEqual(summary["spex"], 0)

    def test_a_missing_invoice_date_goes_to_not_invoiced_yet(self):
        invoice = self._invoice("INV-NODATE")
        BookEvent.objects.filter(pk=invoice.pk).update(invoice_date=None)
        engine.refresh()
        lead = self._lead("INV-NODATE")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.NOT_INVOICED)
        self.assertIsNone(lead.first_seen)

    def test_not_invoiced_beats_a_payment_date(self):
        """
        Rule 3 before rule 4, deliberately.

        An invoice with no date is not something we know enough about to call
        paid, whatever else it carries.
        """
        invoice = self._invoice("INV-BOTH", paid=timezone.now().date())
        BookEvent.objects.filter(pk=invoice.pk).update(invoice_date=None)
        engine.refresh()
        self.assertEqual(
            self._lead("INV-BOTH").bucket, CreditControlLead.Bucket.NOT_INVOICED,
        )

    def test_a_payment_date_closes_the_lead_and_keeps_the_remark(self):
        self._invoice("INV-PAY")
        engine.refresh()
        engine.record_touch(
            self._lead("INV-PAY"), self.bruce,
            {"disposition": "VM", "remark": "left a message on the 3rd"},
        )
        BookEvent.objects.filter(invoice_number="INV-PAY").update(
            payment_date=timezone.now().date(),
        )
        engine.refresh()
        lead = self._lead("INV-PAY")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.DONE)
        self.assertTrue(lead.resolved)
        self.assertIn("Paid", lead.done_reason)
        self.assertEqual(lead.remark, "left a message on the 3rd")

    def test_a_status_that_leaves_pending_closes_the_lead_with_the_live_reason(self):
        self._invoice("INV-CANX")
        engine.refresh()
        BookEvent.objects.filter(invoice_number="INV-CANX").update(
            payment_status=BookEvent.PaymentStatus.CANCELLED,
        )
        engine.refresh()
        lead = self._lead("INV-CANX")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.DONE)
        self.assertEqual(lead.done_reason, "Cancelled")
        self.assertFalse(lead.resolved)

    def test_deleting_the_invoice_takes_the_lead_with_it(self):
        """
        The documented consequence of on_delete=CASCADE, pinned here so it is a
        decision rather than a surprise.

        A hard-deleted booking takes its chase history with it. That is the
        right trade: the alternative is a lead whose `invoice` is gone, which
        every serializer, the dashboard and the engine would each have to guard
        against, in exchange for keeping notes about a booking that no longer
        exists. A booking that is cancelled or refunded is a STATUS change, not
        a delete, and that path keeps everything (see the Cancelled test above).
        """
        self._invoice("INV-GONE")
        engine.refresh()
        self.assertTrue(CreditControlLead.objects.filter(pk="INV-GONE").exists())
        BookEvent.objects.filter(invoice_number="INV-GONE").delete()
        self.assertFalse(CreditControlLead.objects.filter(pk="INV-GONE").exists())

    def test_the_removed_from_source_reason_exists_for_orphans(self):
        """
        The FK carries db_constraint=False, so a raw delete or a table reload
        outside the ORM can leave a lead whose invoice is gone. The reason
        string for that case is unit-tested directly, because the cascade above
        means the engine path cannot reach it.
        """
        self.assertEqual(engine._done_reason(None), "Removed from source")


class HandoffTests(EngineTestCase):
    """Day 4, its three exemptions, and the direction of travel."""

    def _age_the_lead(self, number, *, days):
        """
        Backdate first_seen so the lead has had a shift with its first owner.

        The engine compares first_seen against today rather than the invoice
        date for exactly this reason, so a test that wants a handoff has to
        say the lead was seen on an earlier day.
        """
        CreditControlLead.objects.filter(pk=number).update(
            first_seen=timezone.now() - timedelta(days=days),
        )

    def test_a_new_lead_is_never_handed_off_on_the_same_run(self):
        """Even a badly backdated invoice gets one shift with the team lead."""
        self._invoice("INV-OLD", days_old=90)
        summary = engine.refresh()
        self.assertEqual(summary["handed_off"], 0)
        self.assertEqual(self._lead("INV-OLD").assigned_to, self.bruce)

    def test_day_four_hands_an_untouched_lead_to_an_exec(self):
        self._invoice("INV-4", days_old=constants.HANDOFF_AFTER_DAYS)
        engine.refresh()
        self._age_the_lead("INV-4", days=1)
        summary = engine.refresh()
        self.assertEqual(summary["handed_off"], 1)
        lead = self._lead("INV-4")
        self.assertIn(lead.assigned_to, [self.derek, self.devin, self.ben])
        self.assertIsNotNone(lead.handed_off_at)

    def test_day_three_does_not(self):
        self._invoice("INV-3", days_old=constants.HANDOFF_AFTER_DAYS - 1)
        engine.refresh()
        self._age_the_lead("INV-3", days=1)
        engine.refresh()
        self.assertEqual(self._lead("INV-3").assigned_to, self.bruce)

    def test_an_ongoing_case_stays_with_the_lead_forever(self):
        """
        Every Ongoing disposition pins its lead, and the test enumerates them
        rather than sampling one, because this is the rule most likely to be
        widened by a Config edit and it must hold for the whole group.
        """
        ongoing = [
            label for label, _c, group in constants.DISPOSITION_SEED
            if group == constants.GROUP_ONGOING
        ]
        self.assertTrue(ongoing)
        for index, label in enumerate(ongoing):
            with self.subTest(disposition=label):
                number = f"INV-ONGOING-{index}"
                self._invoice(number, days_old=30)
                engine.refresh()
                engine.record_touch(self._lead(number), self.bruce, {"disposition": label})
                self._age_the_lead(number, days=10)
                engine.refresh()
                self.assertEqual(self._lead(number).assigned_to, self.bruce)
                self.assertIsNone(self._lead(number).handed_off_at)

    def test_an_attempted_or_technical_disposition_does_not_pin(self):
        movable = [
            label for label, _c, group in constants.DISPOSITION_SEED
            if group in (constants.GROUP_ATTEMPTED, constants.GROUP_TECHNICAL)
        ]
        self.assertTrue(movable)
        for index, label in enumerate(movable):
            with self.subTest(disposition=label):
                number = f"INV-MOVE-{index}"
                self._invoice(number, days_old=30)
                engine.refresh()
                engine.record_touch(self._lead(number), self.bruce, {"disposition": label})
                self._age_the_lead(number, days=10)
                engine.refresh()
                self.assertNotEqual(self._lead(number).assigned_to, self.bruce)

    def test_the_handoff_carries_the_work_across(self):
        """
        The exec has to pick up what the lead already did, or they open the call
        with nothing. Only the owner and the stamp may change.
        """
        self._invoice("INV-CARRY", days_old=10)
        engine.refresh()
        engine.record_touch(self._lead("INV-CARRY"), self.bruce, {
            "disposition": "VM",
            "remark": "switchboard closes at 4, try the mobile",
            # next_action is deliberately absent: it was retired from
            # EDITABLE_FIELDS, so a PATCH naming it must be ignored rather than
            # honoured. Asserted below.
            "next_action": "call the mobile after 7pm",
        })
        self._age_the_lead("INV-CARRY", days=5)
        engine.refresh()
        lead = self._lead("INV-CARRY")
        self.assertNotEqual(lead.assigned_to, self.bruce)
        self.assertEqual(lead.disposition, "VM")
        self.assertEqual(lead.remark, "switchboard closes at 4, try the mobile")
        # Retired, so the write was refused and the column stayed empty.
        self.assertEqual(lead.next_action, "")

    def test_a_lead_already_with_an_exec_never_moves_again(self):
        """Handoff runs one way. The pool is a destination, not a carousel."""
        self._invoice("INV-ONCE", days_old=10)
        engine.refresh()
        self._age_the_lead("INV-ONCE", days=5)
        engine.refresh()
        first_owner = self._lead("INV-ONCE").assigned_to
        self.assertNotEqual(first_owner, self.bruce)
        for _ in range(3):
            engine.refresh()
        self.assertEqual(self._lead("INV-ONCE").assigned_to, first_owner)

    def test_the_split_across_execs_is_even(self):
        for index in range(9):
            self._invoice(f"INV-SPLIT-{index}", days_old=10)
        engine.refresh()
        CreditControlLead.objects.all().update(
            first_seen=timezone.now() - timedelta(days=5),
        )
        engine.refresh()
        counts = {}
        for lead in CreditControlLead.objects.filter(
            bucket=CreditControlLead.Bucket.ACTIVE,
        ):
            counts[lead.assigned_to_id] = counts.get(lead.assigned_to_id, 0) + 1
        self.assertNotIn(self.bruce.pk, counts)
        self.assertEqual(sorted(counts.values()), [3, 3, 3])

    def test_an_uneven_starting_load_is_evened_out_not_ignored(self):
        """
        Nine leads onto a pool where one exec already holds four. Measured
        balancing sends the new work to the two who are behind; a stored
        round-robin counter would deal them out regardless.
        """
        for index in range(4):
            invoice = self._invoice(f"INV-PRE-{index}", days_old=1)
            CreditControlLead.objects.create(
                invoice_id=invoice.invoice_number,
                bucket=CreditControlLead.Bucket.ACTIVE,
                assigned_to=self.derek,
                first_seen=timezone.now(),
                handed_off_at=timezone.now(),
            )
        for index in range(4):
            self._invoice(f"INV-NEW-{index}", days_old=10)
        engine.refresh()
        CreditControlLead.objects.filter(
            invoice__invoice_number__startswith="INV-NEW",
        ).update(first_seen=timezone.now() - timedelta(days=5))
        engine.refresh()
        given = list(
            CreditControlLead.objects
            .filter(invoice__invoice_number__startswith="INV-NEW")
            .values_list("assigned_to", flat=True)
        )
        self.assertNotIn(self.derek.pk, given)


class ReactivationTests(EngineTestCase):
    def test_a_lead_the_source_still_owes_comes_back_to_first_touch(self):
        self._invoice("INV-BACK", days_old=20)
        engine.refresh()
        engine.record_touch(
            self._lead("INV-BACK"), self.bruce,
            {"disposition": "VM", "remark": "chased twice"},
        )
        # It leaves, then the payment is reversed and it is owed again.
        BookEvent.objects.filter(invoice_number="INV-BACK").update(
            payment_date=timezone.now().date(),
        )
        engine.refresh()
        self.assertEqual(self._lead("INV-BACK").bucket, CreditControlLead.Bucket.DONE)

        BookEvent.objects.filter(invoice_number="INV-BACK").update(payment_date=None)
        engine.refresh()
        lead = self._lead("INV-BACK")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.ACTIVE)
        self.assertEqual(lead.assigned_to, self.bruce)
        self.assertIsNone(lead.handed_off_at)
        # The work survives; only the clock restarts.
        self.assertEqual(lead.remark, "chased twice")

    def test_spex_is_never_reactivated_into_the_queue(self):
        self._invoice("INV-SPEXBACK", booking="GLD SpEx")
        engine.refresh()
        self.assertEqual(
            self._lead("INV-SPEXBACK").bucket, CreditControlLead.Bucket.SPEX,
        )
        engine.refresh()
        self.assertEqual(
            self._lead("INV-SPEXBACK").bucket, CreditControlLead.Bucket.SPEX,
        )


class IdempotencyTests(EngineTestCase):
    def test_running_twice_changes_nothing_the_second_time(self):
        self._invoice("INV-A", days_old=1)
        self._invoice("INV-B", days_old=40, booking="Speaker")
        self._invoice("INV-C", booking="SLV SpEx")
        self._invoice("INV-D", paid=timezone.now().date())
        engine.refresh()
        before = list(
            CreditControlLead.objects
            .order_by("invoice")
            .values_list("invoice", "bucket", "assigned_to", "first_seen", "done_reason")
        )
        summary = engine.refresh()
        after = list(
            CreditControlLead.objects
            .order_by("invoice")
            .values_list("invoice", "bucket", "assigned_to", "first_seen", "done_reason")
        )
        self.assertEqual(before, after)
        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["handed_off"], 0)


class RosterTests(EngineTestCase):
    def test_no_team_buckets_correctly_and_leaves_leads_unassigned(self):
        """
        A fresh install has no Credit Control team. Bucketing still has to be
        right, and the leads wait for somebody to set the team up, rather than
        the whole pass refusing to run.
        """
        Team.objects.all().delete()
        self._invoice("INV-NOTEAM")
        summary = engine.refresh()
        self.assertEqual(summary["active"], 1)
        self.assertEqual(summary["unassigned"], 1)
        self.assertIsNone(self._lead("INV-NOTEAM").assigned_to)

    def test_a_lead_whose_owner_is_gone_returns_to_first_touch(self):
        self._invoice("INV-ORPHAN", days_old=1)
        engine.refresh()
        CreditControlLead.objects.filter(pk="INV-ORPHAN").update(assigned_to=None)
        engine.refresh()
        self.assertEqual(self._lead("INV-ORPHAN").assigned_to, self.bruce)


class DispositionTests(EngineTestCase):
    def test_a_retired_label_still_resolves_to_a_live_group(self):
        groups = engine.status_group_map()
        for old, new in constants.RETIRED_DISPOSITIONS.items():
            with self.subTest(retired=old):
                self.assertEqual(engine.normalize_disposition(old), new)
                self.assertEqual(groups.get(old), groups.get(new))

    def test_a_retired_label_stored_on_a_lead_is_still_sticky(self):
        """
        "Not Interested" retired into "Disputing Invoice", which is Ongoing. A
        lead worked before the rename must not start moving because of it.
        """
        groups = engine.status_group_map()
        self.assertTrue(engine.is_sticky("Not Interested", groups))

    def test_a_blank_disposition_is_never_sticky(self):
        self.assertFalse(engine.is_sticky("", engine.status_group_map()))

    def test_record_touch_writes_an_audit_row_and_only_the_four_fields(self):
        self._invoice("INV-TOUCH")
        engine.refresh()
        lead = self._lead("INV-TOUCH")
        engine.record_touch(lead, self.bruce, {
            "disposition": "Ringing",
            "remark": "rings out",
            "bucket": CreditControlLead.Bucket.DONE,   # must be ignored
            "assigned_to": self.ben.pk,                # must be ignored
        })
        lead.refresh_from_db()
        self.assertEqual(lead.disposition, "Ringing")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.ACTIVE)
        self.assertEqual(lead.assigned_to, self.bruce)
        self.assertEqual(lead.touches.count(), 1)
        self.assertEqual(lead.touches.first().fields_changed, "disposition,remark")

    def test_record_touch_writes_nothing_when_nothing_changed(self):
        self._invoice("INV-NOOP")
        engine.refresh()
        lead = self._lead("INV-NOOP")
        engine.record_touch(lead, self.bruce, {"disposition": "VM"})
        engine.record_touch(lead, self.bruce, {"disposition": "VM"})
        self.assertEqual(lead.touches.count(), 1)


class DashboardTests(EngineTestCase):
    """
    The sections have to reconcile. A manager who adds up a column and gets a
    different answer from the KPI above it stops trusting the whole page, so
    "every active lead lands in exactly one row" is a test rather than a hope.
    """

    def test_a_future_dated_invoice_still_lands_in_a_bucket(self):
        """
        Found in the live data: an invoice dated tomorrow gives negative days
        pending, matched no age band, and silently dropped out of the aged
        debtor table, leaving it one short of Total Active.
        """
        from . import dashboard

        self._invoice("INV-FUTURE", days_old=-2)
        self._invoice("INV-NORMAL", days_old=10)
        engine.refresh()

        payload = dashboard.build()
        counted = sum(
            sum(cells.values()) for cells in payload["aged_debtor"]["rows"].values()
        )
        self.assertEqual(payload["kpis"]["total_active"], 2)
        self.assertEqual(counted, 2)
        # Nought days old, not un-bucketable.
        self.assertEqual(payload["aged_debtor"]["rows"]["Delegates"]["0-7"], 1)

    def test_every_active_lead_lands_in_exactly_one_aged_row(self):
        from . import dashboard

        self._invoice("INV-D", days_old=5)
        self._invoice("INV-SPK", days_old=40, booking="Speaker")
        self._invoice("INV-ADD", days_old=95, booking="Add-Ons")
        engine.refresh()

        payload = dashboard.build()
        rows = payload["aged_debtor"]["rows"]
        self.assertEqual(sum(sum(c.values()) for c in rows.values()), 3)
        self.assertEqual(sum(rows["Delegates"].values()), 1)
        self.assertEqual(sum(rows["Speakers, Unclassified"].values()), 1)
        self.assertEqual(sum(rows["Add-Ons Only"].values()), 1)

    def test_the_bifurcation_rep_columns_reconcile_with_the_caller_rows(self):
        """
        The rep columns are a second way of slicing the same active set, so they
        have to add up two ways. Down a disposition they must equal its own
        total, and across the whole table they must equal what the per-caller
        section says that person holds. A column that disagrees with the row
        above it is exactly what makes a manager stop trusting the page.
        """
        from . import dashboard

        self._invoice("INV-R1", days_old=5)
        self._invoice("INV-R2", days_old=9)
        self._invoice("INV-R3", days_old=12)
        engine.refresh()

        payload = dashboard.build()
        bifurcation = payload["bifurcation"]

        per_rep = {}
        for cells in bifurcation.values():
            for cell in cells.values():
                reps = cell["reps"]
                self.assertEqual(
                    sum(reps.values()), cell["total"],
                    "a disposition's rep columns must add up to its own total",
                )
                for name, count in reps.items():
                    per_rep[name] = per_rep.get(name, 0) + count

        self.assertEqual(sum(per_rep.values()), payload["kpis"]["total_active"])
        for name, row in payload["status"].items():
            self.assertEqual(
                per_rep.get(name, 0), row["total"],
                f"{name} holds a different number in the two sections",
            )

    def test_a_disposition_nobody_holds_still_carries_an_empty_rep_map(self):
        """
        Every seeded disposition renders even at zero, so the table keeps its
        shape. The frontend reads `reps` per row, and a row seeded without one
        would be the single row that throws on render.
        """
        from . import dashboard

        self._invoice("INV-R4", days_old=5)
        engine.refresh()

        for cells in dashboard.build()["bifurcation"].values():
            for label, cell in cells.items():
                self.assertIn("reps", cell, f"{label} has no reps map")


class ShiftMatrixTests(EngineTestCase):
    def test_calls_are_attributed_to_a_caller_by_email(self):
        """
        The attribution read `user.hubspot_owner_id`, a field the User model
        does not have, so every count came back zero and the matrix looked like
        a quiet week rather than a broken join.
        """
        from django.utils import timezone as tz

        from hubspot.models import HubSpotCall

        today = tz.now().date()
        for i in range(3):
            HubSpotCall.objects.create(
                call_id=f"c{i}", owner_id="99",
                owner_email=self.bruce.email.upper(),  # case must not matter
                occurred_at=tz.now(), shift_date=today,
            )
        matrix = engine.shift_matrix()
        self.assertEqual(matrix["source"], "hubspot")
        bruce = next(r for r in matrix["rows"] if r["user"] == self.bruce.get_full_name())
        self.assertEqual(bruce["counts"][-1], 3)

    def test_calls_by_somebody_off_the_team_are_reported_not_dropped(self):
        from django.utils import timezone as tz

        from hubspot.models import HubSpotCall

        HubSpotCall.objects.create(
            call_id="x1", owner_id="1", owner_email="stranger@example.com",
            occurred_at=tz.now(), shift_date=tz.now().date(),
        )
        matrix = engine.shift_matrix()
        outside = next(r for r in matrix["rows"] if r["user"] == "Outside the team")
        self.assertEqual(outside["counts"][-1], 1)


class LiveRoutingTests(EngineTestCase):
    """
    An invoice reaches a queue on the save that creates it, not on the next pass.

    These use the real post_save receiver rather than calling route_invoice
    directly, because the receiver's registration and its on_commit deferral are
    exactly the parts that would silently stop working.
    """

    def test_a_new_invoice_is_routed_to_the_team_lead_on_save(self):
        from django.test import TestCase as _TC  # noqa: F401  (documents intent)

        # captureOnCommitCallbacks is what runs the deferred routing inside a
        # test's wrapping transaction; without it on_commit never fires and the
        # test would assert that live routing does nothing.
        with self.captureOnCommitCallbacks(execute=True):
            self._invoice("INV-LIVE")
        lead = self._lead("INV-LIVE")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.ACTIVE)
        self.assertEqual(lead.assigned_to, self.bruce)
        self.assertIsNotNone(lead.first_seen)

    def test_marking_it_paid_resolves_the_lead_on_that_save(self):
        with self.captureOnCommitCallbacks(execute=True):
            invoice = self._invoice("INV-LIVEPAID")
        with self.captureOnCommitCallbacks(execute=True):
            invoice.payment_date = timezone.now().date()
            invoice.payment_status = BookEvent.PaymentStatus.PAID
            invoice.save()
        lead = self._lead("INV-LIVEPAID")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.DONE)
        self.assertTrue(lead.resolved)

    def test_spex_is_routed_live_and_stays_off_every_queue(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._invoice("INV-LIVESPEX", booking="GLD SpEx")
        lead = self._lead("INV-LIVESPEX")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.SPEX)
        self.assertIsNone(lead.assigned_to)

    def test_suspending_routing_leaves_the_invoice_for_the_pass(self):
        """What the bulk importers rely on."""
        from credit_control.signals import suspend_routing

        with suspend_routing():
            with self.captureOnCommitCallbacks(execute=True):
                self._invoice("INV-BULK")
        self.assertFalse(CreditControlLead.objects.filter(pk="INV-BULK").exists())
        engine.refresh()
        self.assertEqual(
            self._lead("INV-BULK").bucket, CreditControlLead.Bucket.ACTIVE,
        )

    def test_live_routing_agrees_with_the_full_pass(self):
        """
        The two paths share place_invoice, and this is what proves they have not
        drifted: route one invoice live, run a full pass, and nothing moves.
        """
        with self.captureOnCommitCallbacks(execute=True):
            self._invoice("INV-AGREE", days_old=40, booking="Speaker")
        before = self._lead("INV-AGREE")
        snapshot = (before.bucket, before.assigned_to_id, before.first_seen)
        engine.refresh()
        after = self._lead("INV-AGREE")
        self.assertEqual((after.bucket, after.assigned_to_id, after.first_seen), snapshot)

    def test_a_broken_route_never_breaks_the_save(self):
        """
        A booking has to be recordable even when Credit Control is broken, which
        is the whole reason the receiver swallows its errors.
        """
        from unittest.mock import patch

        with patch("credit_control.signals.route_invoice",
                   side_effect=RuntimeError("boom")):
            with self.captureOnCommitCallbacks(execute=True):
                self._invoice("INV-SAFE")
        # The invoice landed; only its routing was lost, and the pass repairs it.
        self.assertTrue(BookEvent.objects.filter(invoice_number="INV-SAFE").exists())
        engine.refresh()
        self.assertEqual(
            self._lead("INV-SAFE").bucket, CreditControlLead.Bucket.ACTIVE,
        )


class AccessTests(EngineTestCase):
    """
    Who sees what, and who may write it.

    The three questions are separate and this is where that is pinned, because
    getting any two of them confused means one caller can overwrite another's
    work and nobody finds out until a remark goes missing.
    """

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        from teams.models import TeamPermission

        # The team can read every row and record work: `all` is what makes the
        # lead's view the team's rather than their own.
        TeamPermission.objects.create(
            team=self.team, module="credit_control",
            can_view=True, can_update=True, can_all=True,
        )
        for person in (self.bruce, self.derek, self.devin, self.ben):
            person.set_password("x")
            person.save()

        self._invoice("INV-BRUCE", days_old=1)
        engine.refresh()
        # One lead moved to an exec, so there is a row Bruce does not own.
        self._invoice("INV-EXEC", days_old=10)
        engine.refresh()
        CreditControlLead.objects.filter(pk="INV-EXEC").update(
            first_seen=timezone.now() - timedelta(days=5),
        )
        engine.refresh()
        self.exec_lead = self._lead("INV-EXEC")
        self.assertNotEqual(self.exec_lead.assigned_to, self.bruce)

        self.client = APIClient()

    def _as(self, user):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_the_lead_reads_the_whole_team_but_writes_only_his_own(self):
        client = self._as(self.bruce)

        rows = client.get("/api/credit-control/leads/?bucket=active").data["results"]
        owners = {r["assigned_to_name"] for r in rows}
        self.assertGreater(len(owners), 1, "the lead should see the team's queues")

        # His own lead: writable.
        mine = client.patch(
            f"/api/credit-control/leads/INV-BRUCE/", {"remark": "mine"}, format="json",
        )
        self.assertEqual(mine.status_code, 200, mine.content)

        # Somebody else's: readable, and refused on write.
        theirs = client.patch(
            f"/api/credit-control/leads/INV-EXEC/", {"remark": "not mine"}, format="json",
        )
        self.assertEqual(theirs.status_code, 403, theirs.content)
        self.assertEqual(self._lead("INV-EXEC").remark, "")

    def test_can_edit_matches_what_the_server_will_accept(self):
        """
        The flag the table renders from has to agree with the rule the server
        enforces, or the UI offers an input that 403s.
        """
        rows = self._as(self.bruce).get(
            "/api/credit-control/leads/?bucket=active"
        ).data["results"]
        for row in rows:
            expected = row["assigned_to_name"] == (
                self.bruce.get_full_name() or self.bruce.username
            )
            self.assertEqual(row["can_edit"], expected, row["invoice_number"])

    def test_an_exec_sees_only_their_own_queue(self):
        owner = self.exec_lead.assigned_to
        # An exec holds no `all`, recorded as a personal exception to the team.
        from accounts.models import UserPermission

        UserPermission.objects.create(
            user=owner, module="credit_control", can_all=False,
        )
        owner._effective_permissions = None

        rows = self._as(owner).get(
            "/api/credit-control/leads/?bucket=active"
        ).data["results"]
        self.assertTrue(rows)
        self.assertEqual(
            {r["assigned_to_name"] for r in rows},
            {owner.get_full_name() or owner.username},
        )
        self.assertTrue(all(r["can_edit"] for r in rows))

    def test_not_invoiced_and_sponsors_are_admin_only(self):
        for bucket in ("not_invoiced", "spex"):
            with self.subTest(bucket=bucket):
                denied = self._as(self.bruce).get(
                    f"/api/credit-control/leads/?bucket={bucket}"
                )
                self.assertEqual(denied.status_code, 403, denied.content)

    def test_an_admin_sees_the_restricted_lists(self):
        """
        "Admin" here means what accounts.permissions.is_super_admin means: the
        HP account, role=admin, or an all-access team. It is NOT enough to set
        role=admin alone, because crm_permission gates the endpoint on the
        module grid first and a user with no team holds no grant at all. Written
        the naive way, this test failed with a 403 and the code was right.
        """
        from accounts.models import User
        from teams.models import Team

        admins = Team.objects.create(name="Administrators", is_all_access=True)
        admin = User.objects.create_user(
            username="cc-admin", email="cc-admin@iq-hub.com", password="x",
            role=User.Role.ADMIN, team=admins,
        )
        for bucket in ("not_invoiced", "spex"):
            with self.subTest(bucket=bucket):
                allowed = self._as(admin).get(
                    f"/api/credit-control/leads/?bucket={bucket}"
                )
                self.assertEqual(allowed.status_code, 200, allowed.content)


class AttributionTests(EngineTestCase):
    """Who gets the credit when an invoice is paid."""

    def _resolve(self, number):
        BookEvent.objects.filter(invoice_number=number).update(
            payment_date=timezone.now().date(),
            payment_status=BookEvent.PaymentStatus.PAID,
        )
        engine.refresh()
        return self._lead(number)

    def test_the_owner_at_payment_gets_the_credit(self):
        self._invoice("INV-CREDIT", days_old=2)
        engine.refresh()
        engine.record_touch(self._lead("INV-CREDIT"), self.bruce, {
            "disposition": "To Pay - Committed a Date",
            "remark": "promised Friday",
        })
        lead = self._resolve("INV-CREDIT")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.DONE)
        self.assertTrue(lead.resolved)
        self.assertEqual(lead.resolved_by, self.bruce)
        self.assertTrue(lead.resolved_after_effort)
        self.assertEqual(lead.resolved_disposition, "To Pay - Committed a Date")
        self.assertIsNotNone(lead.resolved_at)

    def test_a_lead_nobody_worked_is_credited_to_nobody_s_effort(self):
        """
        An invoice paid before anybody reached the contact is a real outcome and
        counting it as somebody's win would flatter the whole team.
        """
        self._invoice("INV-NOEFFORT", days_old=2)
        engine.refresh()
        lead = self._resolve("INV-NOEFFORT")
        self.assertTrue(lead.resolved)
        self.assertFalse(lead.resolved_after_effort)
        # Still owned, so the row is attributable, just not as effort.
        self.assertEqual(lead.resolved_by, self.bruce)

    def test_the_credit_is_stamped_once_and_never_moves(self):
        self._invoice("INV-ONCE-PAID", days_old=2)
        engine.refresh()
        lead = self._resolve("INV-ONCE-PAID")
        first_at, first_by = lead.resolved_at, lead.resolved_by_id

        # Reassigning it afterwards must not rewrite who collected it, and
        # neither must any number of later passes.
        CreditControlLead.objects.filter(pk="INV-ONCE-PAID").update(assigned_to=self.ben)
        for _ in range(3):
            engine.refresh()
        again = self._lead("INV-ONCE-PAID")
        self.assertEqual(again.resolved_at, first_at)
        self.assertEqual(again.resolved_by_id, first_by)


class LiveNotInvoicedTests(EngineTestCase):
    """
    A NEW BOOKING ONLY REACHES A QUEUE IF IT CARRIES AN INVOICE DATE.

    This is the common case in practice, because a booking is usually created
    before it is invoiced, so the live path has to get it right or every new
    booking would land on the team lead as something to chase before there is
    anything to chase. The rule is precedence 3 in the engine and these assert
    it holds on the live path too, not just in a full pass.
    """

    def _uninvoiced(self, number):
        """A booking created without an invoice date, which is how they arrive."""
        return BookEvent.objects.create(
            invoice_number=number,
            event_code="ISCC",
            booking_code="Delegate",
            invoice_date=None,
            payment_status=BookEvent.PaymentStatus.PENDING,
            company_name=f"Company {number}",
        )

    def test_a_new_booking_with_no_invoice_date_goes_to_not_invoiced(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._uninvoiced("INV-NOINV-LIVE")
        lead = self._lead("INV-NOINV-LIVE")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.NOT_INVOICED)
        # Nobody's queue, and no clock started.
        self.assertIsNone(lead.assigned_to)
        self.assertIsNone(lead.first_seen)

    def test_adding_the_invoice_date_moves_it_to_the_team_lead(self):
        """
        And the clock starts THEN, not when the booking was created, so a
        booking that sat uninvoiced for a fortnight still gets its first shift
        with the team lead rather than being handed off immediately.
        """
        with self.captureOnCommitCallbacks(execute=True):
            invoice = self._uninvoiced("INV-BECOMES")
        self.assertEqual(
            self._lead("INV-BECOMES").bucket, CreditControlLead.Bucket.NOT_INVOICED,
        )

        with self.captureOnCommitCallbacks(execute=True):
            invoice.invoice_date = timezone.now().date() - timedelta(days=14)
            invoice.save()

        lead = self._lead("INV-BECOMES")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.ACTIVE)
        self.assertEqual(lead.assigned_to, self.bruce)
        self.assertIsNotNone(lead.first_seen)
        self.assertIsNone(lead.handed_off_at)

    def test_losing_the_invoice_date_sends_it_back_and_keeps_the_remark(self):
        with self.captureOnCommitCallbacks(execute=True):
            invoice = self._invoice("INV-UNINVOICED", days_old=1)
        engine.record_touch(
            self._lead("INV-UNINVOICED"), self.bruce,
            {"disposition": "VM", "remark": "left a message"},
        )
        with self.captureOnCommitCallbacks(execute=True):
            invoice.invoice_date = None
            invoice.save()
        lead = self._lead("INV-UNINVOICED")
        self.assertEqual(lead.bucket, CreditControlLead.Bucket.NOT_INVOICED)
        self.assertEqual(lead.remark, "left a message")


class ShiftMatrixScopeTests(EngineTestCase):
    """
    The calls matrix is a permission boundary, not a display preference.

    Every other section of the dashboard scoped itself to the caller asking and
    this one did not, so an exec who could see only their own leads was still
    shown every colleague's call count.
    """

    def setUp(self):
        super().setUp()
        from django.utils import timezone as tz

        from hubspot.models import HubSpotCall

        today = tz.now().date()
        for i, person in enumerate((self.bruce, self.derek, self.devin)):
            for n in range(i + 1):
                HubSpotCall.objects.create(
                    call_id=f"{person.username}-{n}", owner_id=str(i),
                    owner_email=person.email, occurred_at=tz.now(),
                    shift_date=today,
                )
        # Somebody outside the roster, whose calls must never reach a scoped view.
        HubSpotCall.objects.create(
            call_id="stranger-1", owner_id="99", owner_email="stranger@example.com",
            occurred_at=tz.now(), shift_date=today,
        )

    def test_unscoped_shows_the_whole_team_and_the_off_team_row(self):
        matrix = engine.shift_matrix()
        names = {r["user"] for r in matrix["rows"]}
        self.assertIn(self.bruce.get_full_name(), names)
        self.assertIn(self.derek.get_full_name(), names)
        self.assertIn("Outside the team", names)

    def test_scoped_shows_exactly_one_caller(self):
        matrix = engine.shift_matrix(only=self.derek)
        self.assertEqual([r["user"] for r in matrix["rows"]], [self.derek.get_full_name()])
        self.assertEqual(matrix["rows"][0]["counts"][-1], 2)

    def test_scoped_never_leaks_the_off_team_aggregate(self):
        """
        It is a bucket of other people's calls, so leaving it in would leak the
        same thing one level up.
        """
        matrix = engine.shift_matrix(only=self.derek)
        self.assertNotIn("Outside the team", {r["user"] for r in matrix["rows"]})

    def test_the_dashboard_scopes_it_with_everything_else(self):
        scoped = dashboard.build(scoped_to=self.devin)
        self.assertEqual(
            [r["user"] for r in scoped["shift_matrix"]["rows"]],
            [self.devin.get_full_name()],
        )
        full = dashboard.build()
        self.assertGreater(len(full["shift_matrix"]["rows"]), 1)


class HandoverContextTests(EngineTestCase):
    """
    What the receiving caller inherits, and why it has to be frozen.

    The live disposition and remark travel with the lead, which is right. But
    they become the NEW owner's editable fields, so the moment they type their
    own note the reason they were given it is gone from view. These assert the
    snapshot survives that.
    """

    def _hand_over(self, number):
        self._invoice(number, days_old=10)
        engine.refresh()
        engine.record_touch(self._lead(number), self.bruce, {
            "disposition": "VM",
            "remark": "switchboard closes at 4, try the mobile",
        })
        CreditControlLead.objects.filter(pk=number).update(
            first_seen=timezone.now() - timedelta(days=5),
        )
        engine.refresh()
        return self._lead(number)

    def test_the_handover_records_who_had_it_and_what_they_logged(self):
        lead = self._hand_over("INV-CTX")
        self.assertNotEqual(lead.assigned_to, self.bruce)
        self.assertEqual(lead.handed_off_from, self.bruce)
        self.assertEqual(lead.handoff_disposition, "VM")
        self.assertEqual(
            lead.handoff_remark, "switchboard closes at 4, try the mobile",
        )
        self.assertIsNotNone(lead.handed_off_at)

    def test_the_receiving_caller_typing_over_it_does_not_lose_the_context(self):
        """The whole reason the snapshot exists."""
        lead = self._hand_over("INV-CTX2")
        receiver = lead.assigned_to

        engine.record_touch(lead, receiver, {
            "disposition": "Ringing",
            "remark": "tried the mobile, rang out",
        })
        after = self._lead("INV-CTX2")

        # The live fields are the new caller's.
        self.assertEqual(after.remark, "tried the mobile, rang out")
        self.assertEqual(after.disposition, "Ringing")
        # What Bruce left is still there to read.
        self.assertEqual(after.handed_off_from, self.bruce)
        self.assertEqual(after.handoff_disposition, "VM")
        self.assertEqual(
            after.handoff_remark, "switchboard closes at 4, try the mobile",
        )

    def test_the_full_history_names_both_callers(self):
        lead = self._hand_over("INV-CTX3")
        engine.record_touch(lead, lead.assigned_to, {"remark": "rang out"})
        touches = list(
            CreditControlLead.objects.get(pk="INV-CTX3")
            .touches.select_related("user").order_by("created_at")
        )
        self.assertEqual(
            [t.user for t in touches], [self.bruce, self._lead("INV-CTX3").assigned_to],
        )

    def test_a_lead_never_handed_over_carries_no_handover_context(self):
        self._invoice("INV-NOCTX", days_old=1)
        engine.refresh()
        lead = self._lead("INV-NOCTX")
        self.assertIsNone(lead.handed_off_from)
        self.assertEqual(lead.handoff_remark, "")


class ManualJobTests(EngineTestCase):
    """
    The per-job Run buttons. Admin only, and a whitelist rather than a command
    name off the request, because `call_command` with caller-supplied input is
    remote code execution wearing a management command's clothes.
    """

    def _as(self, user):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _admin(self):
        from accounts.models import User
        from teams.models import Team

        admins = Team.objects.create(name="Admins", is_all_access=True)
        return User.objects.create_user(
            username="job-admin", email="job-admin@iq-hub.com", password="x",
            role=User.Role.ADMIN, team=admins,
        )

    def test_an_admin_can_run_the_routing_pass(self):
        self._invoice("INV-MANUAL", days_old=1)
        resp = self._as(self._admin()).post("/api/credit-control/run/routing/")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["ok"])
        self.assertIn("Routed", resp.data["output"])
        self.assertTrue(CreditControlLead.objects.filter(pk="INV-MANUAL").exists())

    def test_a_caller_cannot_run_jobs(self):
        from teams.models import TeamPermission

        TeamPermission.objects.create(
            team=self.team, module="credit_control", can_view=True, can_update=True,
        )
        resp = self._as(self.bruce).post("/api/credit-control/run/routing/")
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_an_unknown_job_is_refused_rather_than_executed(self):
        resp = self._as(self._admin()).post("/api/credit-control/run/flush_all/")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("Unknown job", resp.data["detail"])

    def test_every_scheduled_job_has_a_runnable_key(self):
        """
        The button on the page and the whitelist on the server must agree, or a
        Run button 400s on a job that is genuinely scheduled.
        """
        from credit_control.views import CreditControlViewSet

        keys = {row["key"] for row in dashboard.schedule()}
        self.assertTrue(keys)
        self.assertLessEqual(keys, set(CreditControlViewSet.RUNNABLE_JOBS))


class PaymentSheetImportTests(EngineTestCase):
    """
    The one-shot sheet import, covering the four things in it that can be wrong
    in a way no other test would catch.
    """

    def _row(self, invoice, *, tab="Bruce", delegate="A Person", email="a@x.com",
             disposition="", remark="", next_action="", updated=None,
             callback=None, invoice_type="", type_notes=""):
        from credit_control.management.commands import import_payment_sheet as cmd

        return {
            "invoice": invoice, "tab": tab, "owner": getattr(self, tab.lower()),
            "delegate": delegate, "email": email,
            "disposition": cmd.clean_disposition(disposition),
            "remark": remark, "next_action": next_action,
            "callback": callback, "updated": updated,
            "invoice_type": invoice_type, "type_notes": type_notes,
        }

    def test_a_test_booking_is_recognised_by_its_domain(self):
        from credit_control.management.commands import import_payment_sheet as cmd

        self.assertTrue(cmd.is_test_row("wupisa@mailinator.com"))
        self.assertTrue(cmd.is_test_row("Parker.Simpson@iQ-Hub.com"))
        self.assertFalse(cmd.is_test_row("ben.yu@nrc-cnrc.gc.ca"))
        self.assertFalse(cmd.is_test_row(""))

    def test_the_sheets_text_dates_parse(self):
        from credit_control.management.commands import import_payment_sheet as cmd

        stamp = cmd.sheet_datetime("19-Aug-2026 23:50:36")
        self.assertEqual((stamp.year, stamp.month, stamp.day), (2026, 8, 19))
        self.assertIsNotNone(stamp.tzinfo)
        # Day first, and the column has no ambiguous value in it.
        self.assertEqual(cmd.sheet_date("27/8/2026").month, 8)
        # Unreadable is None rather than an exception; one bad stamp must not
        # stop a migration.
        self.assertIsNone(cmd.sheet_datetime("not a date"))
        self.assertIsNone(cmd.sheet_date(""))

    def test_a_retired_label_migrates_and_a_bucket_label_is_dropped(self):
        from credit_control.management.commands import import_payment_sheet as cmd

        self.assertEqual(cmd.clean_disposition("Not Interested"), "Disputing Invoice")
        self.assertEqual(cmd.clean_disposition("Out of TimeZone"), "Out of Time Zone")
        # A bucket is not a disposition, so the label goes and the lead reads
        # as untouched.
        self.assertEqual(cmd.clean_disposition("Not Invoiced Yet"), "")
        self.assertEqual(cmd.clean_disposition("VM"), "VM")

    def test_newest_wins_and_the_losing_callers_work_survives(self):
        """
        The collapse is the one place work could be silently destroyed, because
        two callers' rows become one lead and only one disposition can survive.
        """
        from credit_control.management.commands import import_payment_sheet as cmd

        older = self._row(
            "SCE26LDN-2867", tab="Ben", delegate="Ekaterine Kakhidze",
            disposition="NC - HubSpot Restrictions", remark="27Aug-Error",
            updated=timezone.now() - timedelta(days=5),
        )
        newer = self._row(
            "SCE26LDN-2867", tab="Bruce", delegate="Alexander Khvedelidze",
            disposition="Not Interested", remark="20Aug-Responded via whatsapp",
            next_action="Chase Tuesday", updated=timezone.now(),
        )
        won = cmd.collapse([older, newer])

        self.assertEqual(won["owner"], self.bruce)
        self.assertEqual(won["disposition"], "Disputing Invoice")
        self.assertEqual(won["folded"], 1)
        # The winner's own text, then its next action, then the losing row
        # tagged with the delegate AND the disposition that row held, which is
        # otherwise the one thing the collapse would destroy.
        self.assertIn("20Aug-Responded via whatsapp", won["remark"])
        self.assertIn("Next: Chase Tuesday", won["remark"])
        self.assertIn("[Ekaterine Kakhidze, NC - HubSpot Restrictions]", won["remark"])
        self.assertIn("27Aug-Error", won["remark"])

    def test_an_unstamped_row_never_outranks_a_worked_one(self):
        from credit_control.management.commands import import_payment_sheet as cmd

        blank = self._row("X-1", tab="Devin", delegate="Nobody", updated=None)
        worked = self._row(
            "X-1", tab="Derek", delegate="Somebody", disposition="VM",
            updated=timezone.now() - timedelta(days=40),
        )
        self.assertEqual(cmd.collapse([blank, worked])["owner"], self.derek)

    def test_the_edition_fence_is_the_invoice_prefix(self):
        from credit_control.management.commands import import_payment_sheet as cmd

        self.assertEqual(cmd.edition_of("DLG26BUE-2853"), "dlg26bue")
        self.assertNotEqual(cmd.edition_of("DLG26BUE-2853"), cmd.edition_of("HFE27GER-2842"))

    def test_a_touch_is_one_call_by_one_user_to_one_lead(self):
        """
        The definition the whole module now hangs on. my_calls counts THIS
        user's rows on THIS lead, and nobody else's.
        """
        from credit_control.models import CreditControlTouch
        from credit_control.serializers import LeadSerializer

        self._invoice("ISCC-1", days_old=10)
        engine.refresh()
        lead = self._lead("ISCC-1")
        for user, count in ((self.bruce, 3), (self.derek, 1)):
            for _ in range(count):
                CreditControlTouch.objects.create(
                    lead=lead, user=user, disposition="VM", fields_changed="disposition",
                )

        def seen_by(user):
            counts = dict(
                CreditControlTouch.objects
                .filter(lead=lead, user=user)
                .values_list("lead_id")
                .annotate(n=Count("id"))
            )
            return LeadSerializer(lead, context={"my_calls": counts}).data["my_calls"]

        self.assertEqual(seen_by(self.bruce), 3)
        self.assertEqual(seen_by(self.derek), 1)
        # Zero and not None: this table is the CRM's own, so an absent row
        # genuinely means no call was logged.
        self.assertEqual(seen_by(self.devin), 0)
