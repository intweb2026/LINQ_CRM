"""
book_delegate/tests_partial_transfer.py
────────────────────────────────────────
POST /api/delegates/transfer/ — moving SOME of an invoice's delegates to another
event in one request.

THE CASE THIS COVERS
An invoice carrying five delegates where only two are moving. Before this endpoint
the only way to express it was two separate single-delegate transfers, the second
reusing the invoice number the first created — which worked, but was not atomic, and
made the source invoice's status depend on which delegate happened to go last.

WHAT MUST HOLD, AND WHY EACH ASSERTION IS SEPARATE
payment_status resolves as COALESCE(delegate override, invoice value), so "the row
reads Credit Transferred" can be true for two different reasons. A partial transfer
must write the OVERRIDE and leave the invoice column alone (the three delegates
staying behind are still booked and paid); a whole-invoice transfer must write the
INVOICE and clear the overrides. Checking only the resolved value would pass in both
cases and catch neither mistake, so the tests read the two columns separately —
the same discipline as tests_delegate_transfer.py.

    python manage.py test book_delegate.tests_partial_transfer
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from events.models import Event
from teams.models import Team

User = get_user_model()

BATCH = BookDelegateViewSet.as_view({"post": "transfer_batch"})

SRC_CODE = "PXF - AA"
DST_CODE = "PXF - BB"


class PartialBatchTestBase(TestCase):
    """
    One invoice, five delegates — the shape from the report. Ada and Grace are the
    two that move; Alan, Edsger and Barbara stay.
    """

    @classmethod
    def setUpTestData(cls):
        cls.all_access = Team.objects.create(name="pxf_all", is_all_access=True)
        cls.user = User.objects.create_user(
            username="pxf_admin", password="x", role="admin", email="pa@iq-hub.com",
        )
        cls.user.team = cls.all_access
        cls.user.save()

    def setUp(self):
        self.factory = APIRequestFactory()
        self.src_event = Event.objects.create(
            event_code=SRC_CODE, official_event_name="Source Event",
            event_date="2025-06-01",
        )
        self.dst_event = Event.objects.create(
            event_code=DST_CODE, official_event_name="Destination Event",
            event_date="2026-09-15",
        )
        self.invoice = BookEvent.objects.create(
            invoice_number="PSRC-1", event_code=SRC_CODE, edition=2025,
            payment_status="Paid", booking_code="Delegate", ticket_tier="SEB",
            payment_type="Bank", paid_or_free="Paid", company_name="Acme Ltd",
            contact_email="ada@acme.test", currency="USD",
            source=BookEvent.Source.WEBSITE,
        )
        self.people = {}
        for n, (first, last) in enumerate([
            ("Ada", "Lovelace"), ("Grace", "Hopper"), ("Alan", "Turing"),
            ("Edsger", "Dijkstra"), ("Barbara", "Liskov"),
        ], start=1):
            self.people[first.lower()] = BookDelegate.objects.create(
                invoice=self.invoice, event_code=SRC_CODE, edition=2025,
                first_name=first, last_name=last,
                email=f"{first.lower()}@acme.test",
                booking_code="Delegate", delegate_number=n,
                attendance=BookDelegate.Attendance.CONFIRMED,
                reference="OC250722019137000",
            )
        self.moving = [self.people["ada"], self.people["grace"]]
        self.staying = [self.people["alan"], self.people["edsger"],
                        self.people["barbara"]]

    def batch(self, delegates=None, user=None, **body):
        delegates = self.moving if delegates is None else delegates
        payload = {
            "delegate_ids": [d.id for d in delegates],
            "target_event_code": DST_CODE,
            "invoice_number": "PDST-1",
        }
        payload.update(body)
        req = self.factory.post("/api/delegates/transfer/", payload, format="json")
        force_authenticate(req, user=user or self.user)
        resp = BATCH(req)
        resp.render()
        return resp


class TwoOfFiveTests(PartialBatchTestBase):
    """Two delegates out of five — the reported case."""

    def test_it_reports_what_moved_and_what_stayed(self):
        r = self.batch()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["count"], 2)
        self.assertEqual(r.data["source"]["scope"], "delegate")
        self.assertEqual(r.data["source"]["left_behind"], 3)
        self.assertEqual(r.data["created"]["invoice_number"], "PDST-1")
        self.assertEqual(r.data["created"]["event_code"], DST_CODE)

    def test_both_moved_rows_land_on_one_new_invoice(self):
        self.batch()
        dest = BookEvent.objects.get(invoice_number="PDST-1")
        self.assertEqual(dest.event_code, DST_CODE)
        emails = sorted(dest.delegates.values_list("email", flat=True))
        self.assertEqual(emails, ["ada@acme.test", "grace@acme.test"])

    def test_the_moved_rows_carry_the_status_as_an_override(self):
        self.batch()
        for d in self.moving:
            d.refresh_from_db()
            self.assertEqual(d.delegate_payment_status, "Credit Transferred", d.email)

    def test_the_source_invoice_status_is_left_alone(self):
        # The three staying are still booked and paid. Flipping the invoice would
        # relabel all five.
        self.batch()
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, "Paid")

    def test_the_three_staying_are_untouched(self):
        self.batch()
        for d in self.staying:
            d.refresh_from_db()
            self.assertIsNone(d.delegate_payment_status, d.email)
            # invoice_id holds the invoice NUMBER: the FK is declared
            # to_field="invoice_number" (book_delegate/models.py:22-28).
            self.assertEqual(d.invoice_id, self.invoice.invoice_number, d.email)
            self.assertEqual(d.event_code, SRC_CODE, d.email)
            self.assertEqual(d.reference, "OC250722019137000", d.email)

    def test_the_source_rows_are_not_moved_or_deleted(self):
        self.batch()
        self.assertEqual(self.invoice.delegates.count(), 5)

    def test_the_breadcrumbs_are_written_on_both_sides(self):
        self.batch()
        for d in self.moving:
            d.refresh_from_db()
            self.assertIn("Transferred to PXF - BB", d.reference, d.email)
        for d in BookEvent.objects.get(invoice_number="PDST-1").delegates.all():
            self.assertIn("Transferred from", d.reference, d.email)

    def test_attendance_restarts_on_the_new_event(self):
        self.batch()
        dest = BookEvent.objects.get(invoice_number="PDST-1")
        for d in dest.delegates.all():
            self.assertEqual(d.attendance, BookDelegate.Attendance.PENDING, d.email)

    def test_it_logs_one_entry_for_the_whole_move(self):
        from accounts.models import ActionLog
        self.batch()
        logs = ActionLog.objects.filter(action__startswith="Transferred")
        self.assertEqual(logs.count(), 1)
        log = logs.first()
        self.assertEqual(log.action, f"Transferred 2 delegates to {DST_CODE}")
        self.assertIn("ada@acme.test", log.details)
        self.assertIn("grace@acme.test", log.details)
        self.assertIn("3 delegate(s) left", log.details)


class WholeInvoiceTests(PartialBatchTestBase):
    """
    All five at once. The status then belongs ON the invoice — the same outcome the
    single-delegate endpoint produces for a one-delegate invoice, and the reason
    "is anything left" is decided over the whole set rather than per delegate.
    """

    def test_the_invoice_carries_the_status(self):
        r = self.batch(delegates=list(self.people.values()))
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["source"]["scope"], "invoice")
        self.assertEqual(r.data["source"]["left_behind"], 0)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, "Credit Transferred")

    def test_no_stale_override_shadows_it(self):
        self.batch(delegates=list(self.people.values()))
        for d in self.people.values():
            d.refresh_from_db()
            self.assertIsNone(d.delegate_payment_status, d.email)


class OrderIndependenceTests(PartialBatchTestBase):
    """
    The bug the atomic endpoint exists to prevent: run one-at-a-time, and whichever
    delegate goes LAST is the one that empties the invoice, so the invoice's status
    depended on transfer order. Batched, the answer is the same either way.
    """

    def test_the_outcome_does_not_depend_on_selection_order(self):
        forwards = self.batch(delegates=[self.people["ada"], self.people["grace"]])
        self.assertEqual(forwards.data["source"]["scope"], "delegate")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, "Paid")

        # The remaining three, given in reverse. Still not the whole invoice's worth
        # counting the two already gone... but those two ARE gone, so this empties
        # what is left and the status moves onto the invoice.
        backwards = self.batch(
            delegates=[self.people["barbara"], self.people["edsger"],
                       self.people["alan"]],
            invoice_number="PDST-2",
        )
        self.assertEqual(backwards.status_code, 201, backwards.data)
        # Not "invoice": the two rows transferred away in the first call are still
        # ON this invoice — the pair is the audit trail — so the invoice is never
        # empty and the status stays per-delegate. This is the existing rule, and
        # it is why `left_behind` counts rows rather than active bookings.
        self.assertEqual(backwards.data["source"]["left_behind"], 2)
        self.assertEqual(backwards.data["source"]["scope"], "delegate")


class PerDelegateValuesTests(PartialBatchTestBase):
    """
    A group can disagree. Where they do, the destination invoice takes the source
    invoice's value and the differences ride along as overrides — a transfer must
    not quietly put everyone on one tier.
    """

    def test_a_shared_override_lands_on_the_new_invoice(self):
        for d in self.moving:
            d.delegate_ticket_tier = "SEB2"
            d.save()
        self.batch()
        dest = BookEvent.objects.get(invoice_number="PDST-1")
        self.assertEqual(dest.ticket_tier, "SEB2")
        for d in dest.delegates.all():
            self.assertIsNone(d.delegate_ticket_tier, d.email)

    def test_differing_overrides_are_preserved_per_delegate(self):
        self.people["ada"].delegate_ticket_tier = "SEB2"
        self.people["ada"].save()
        self.people["grace"].delegate_ticket_tier = "SEB3"
        self.people["grace"].save()
        self.batch()
        dest = BookEvent.objects.get(invoice_number="PDST-1")
        # Neither wins the invoice: it keeps the source invoice's tier.
        self.assertEqual(dest.ticket_tier, "SEB")
        got = {d.email: d.delegate_ticket_tier for d in dest.delegates.all()}
        self.assertEqual(got["ada@acme.test"], "SEB2")
        self.assertEqual(got["grace@acme.test"], "SEB3")

    def test_a_partial_override_does_not_reach_the_invoice(self):
        # Only one of the two carries it, so it is that delegate's, not the group's.
        self.people["ada"].delegate_payment_type = "Card"
        self.people["ada"].save()
        self.batch()
        dest = BookEvent.objects.get(invoice_number="PDST-1")
        self.assertEqual(dest.payment_type, "Bank")
        got = {d.email: d.delegate_payment_type for d in dest.delegates.all()}
        self.assertEqual(got["ada@acme.test"], "Card")
        self.assertIsNone(got["grace@acme.test"])


class BatchValidationTests(PartialBatchTestBase):

    def test_an_empty_selection_is_refused(self):
        r = self.batch(delegate_ids=[])
        self.assertEqual(r.status_code, 400)
        self.assertIn("non-empty", r.data["detail"])

    def test_a_missing_delegate_ids_key_is_refused(self):
        req = self.factory.post("/api/delegates/transfer/", {
            "target_event_code": DST_CODE, "invoice_number": "PDST-1",
        }, format="json")
        force_authenticate(req, user=self.user)
        r = BATCH(req)
        r.render()
        self.assertEqual(r.status_code, 400)

    def test_an_unknown_id_is_a_404_naming_it(self):
        r = self.batch(delegate_ids=[self.people["ada"].id, 99_000_001])
        self.assertEqual(r.status_code, 404)
        self.assertIn("99000001", r.data["detail"])

    def test_two_source_invoices_are_refused(self):
        other_invoice = BookEvent.objects.create(
            invoice_number="PSRC-2", event_code=SRC_CODE, edition=2025,
            payment_status="Paid",
        )
        stranger = BookDelegate.objects.create(
            invoice=other_invoice, event_code=SRC_CODE, edition=2025,
            first_name="Ken", last_name="Thompson", email="ken@acme.test",
        )
        r = self.batch(delegates=[self.people["ada"], stranger])
        self.assertEqual(r.status_code, 400)
        self.assertIn("different invoices", r.data["detail"])

    # No "same person selected twice" test: it is not reachable. Every selected
    # delegate is on one invoice and BookDelegate declares
    # unique_together = [("invoice", "email")], so the database refuses two rows for
    # one email on one invoice long before a transfer could select them. Writing the
    # test proved it — setting up the fixture raised IntegrityError, not a 409.

    def test_a_target_that_is_the_source_is_refused(self):
        r = self.batch(target_event_code=SRC_CODE)
        self.assertEqual(r.status_code, 400)

    def test_a_number_taken_on_another_event_is_refused(self):
        BookEvent.objects.create(
            invoice_number="PDST-1", event_code="PXF - CC", edition=2026,
            payment_status="Paid",
        )
        r = self.batch()
        self.assertEqual(r.status_code, 409)
        self.assertIn("already exists on", r.data["detail"])

    def test_a_refusal_moves_nothing(self):
        self.batch(target_event_code="NOPE - ZZ")
        for d in self.people.values():
            d.refresh_from_db()
            self.assertIsNone(d.delegate_payment_status, d.email)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, "Paid")
        self.assertFalse(BookEvent.objects.filter(invoice_number="PDST-1").exists())

    def test_joining_an_invoice_that_already_has_one_of_them_is_refused(self):
        # Ada moves alone first, then a second call tries to take both.
        self.batch(delegates=[self.people["ada"]])
        r = self.batch(delegates=[self.people["ada"], self.people["grace"]])
        self.assertEqual(r.status_code, 409)
        self.assertIn("already on invoice", r.data["detail"])

    def test_the_rest_can_join_an_invoice_a_previous_transfer_created(self):
        self.batch(delegates=[self.people["ada"]])
        r = self.batch(delegates=[self.people["grace"]])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["created"]["reused_invoice"])
        dest = BookEvent.objects.get(invoice_number="PDST-1")
        self.assertEqual(dest.delegates.count(), 2)
