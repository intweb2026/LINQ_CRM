"""
book_delegate/tests_delegate_transfer.py
─────────────────────────────────────────
POST /api/delegates/{id}/transfer/ — moving one delegate's credit to another event.

THE SHAPE THIS MIRRORS
It is not a new idea: ~200 transfers already sit in the database, made by hand in
Zoho. One real chain, followed through four bookings for the same delegate:

    AIU25HOU-2804  AIU 2025       Credit Transferred   ref "… / Transferred to FAU'25"
    FAU25USA-2587  FAU 2025       Credit Transferred   ref "Transferred from - AIU25"
    RGU26CAL-2011  RGU - AD 2026  Credit Transferred   ref "Transferred from FAU'25"
    Inv-19251      AIU - AD 2026  Paid (Transferred)   ref "Transferred from - RGU - AD26"

So: the booking left behind reads Credit Transferred, the booking created reads Paid
(Transferred), and a booking transferred away AGAIN flips from Paid (Transferred) to
Credit Transferred. Both rows always survive — the pair IS the audit trail. Every
assertion below is anchored to that observed shape rather than to a preference.

WHY THE TESTS ARE THIS PICKY ABOUT WHICH ROW HOLDS THE STATUS
payment_status resolves as COALESCE(delegate override, invoice value). A transfer
that wrote the invoice while leaving a stale override on the delegate would look
correct in the database and show the OLD status in the table — so the tests check
the override and the invoice column separately, never just the effective value.

    python manage.py test book_delegate.tests_delegate_transfer
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate


from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from events.models import Event
from teams.models import Team, TeamPermission

User = get_user_model()

TRANSFER = BookDelegateViewSet.as_view({"post": "transfer"})

SRC_CODE = "XFR - AA"
DST_CODE = "XFR - BB"


class TransferTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.all_access = Team.objects.create(
            name="xfr_all", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="xfr_admin", password="x", role="admin", email="xa@iq-hub.com",
        )
        cls.user.team = cls.all_access
        cls.user.save()

        cls.rep = User.objects.create_user(
            username="xfr_rep", password="x", role="sales", email="xr@iq-hub.com",
            first_name="Dest", last_name="Rep",
        )

    def setUp(self):
        self.factory = APIRequestFactory()
        self.src_event = Event.objects.create(
            event_code=SRC_CODE, official_event_name="Source Event",
            event_date="2025-06-01",
        )
        self.dst_event = Event.objects.create(
            event_code=DST_CODE, official_event_name="Destination Event",
            event_date="2026-09-15", sales_executive=self.rep,
        )
        self.invoice = BookEvent.objects.create(
            invoice_number="SRC-1", event_code=SRC_CODE, edition=2025,
            payment_status="Paid", booking_code="Speaker", ticket_tier="SEB",
            payment_type="Bank", paid_or_free="Paid", company_name="Acme Ltd",
            contact_email="ada@acme.test", currency="USD",
            source=BookEvent.Source.WEBSITE,
        )
        self.delegate = BookDelegate.objects.create(
            invoice=self.invoice, event_code=SRC_CODE, edition=2025,
            first_name="Ada", last_name="Lovelace", email="ada@acme.test",
            phone_number="+1 555", position="CTO", booking_code="Speaker",
            delegate_number=2, discount="0.20", add_ons="Workshop",
            attendance=BookDelegate.Attendance.CONFIRMED,
            reference="OC250722019137000",
        )

    def transfer(self, delegate=None, user=None, **body):
        delegate = delegate or self.delegate
        payload = {"target_event_code": DST_CODE, "invoice_number": "DST-1"}
        payload.update(body)
        req = self.factory.post(
            f"/api/delegates/{delegate.id}/transfer/", payload, format="json")
        force_authenticate(req, user=user or self.user)
        resp = TRANSFER(req, pk=delegate.id)
        resp.render()
        return resp


class TransferHappyPathTests(TransferTestBase):

    def test_the_two_statuses_land_on_the_right_rows(self):
        resp = self.transfer()
        self.assertEqual(resp.status_code, 201, resp.content)

        self.invoice.refresh_from_db()
        self.delegate.refresh_from_db()
        # Sole delegate: the status belongs on the invoice, and the override must be
        # CLEARED or it would shadow it and the row would still read "Paid".
        self.assertEqual(self.invoice.payment_status, "Credit Transferred")
        self.assertIsNone(self.delegate.delegate_payment_status)

        new = BookDelegate.objects.get(invoice__invoice_number="DST-1")
        self.assertEqual(new.invoice.payment_status, "Paid (Transferred)")
        self.assertIsNone(new.delegate_payment_status)

    def test_the_original_booking_is_not_deleted_or_moved(self):
        self.transfer()
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.event_code, SRC_CODE)
        self.assertTrue(
            BookDelegate.objects.filter(pk=self.delegate.pk).exists(),
            "the transferred-from row must survive — the pair is the audit trail",
        )

    def test_the_new_booking_copies_the_delegate(self):
        self.transfer()
        new = BookDelegate.objects.get(invoice__invoice_number="DST-1")
        self.assertEqual(
            (new.first_name, new.last_name, new.email, new.phone_number, new.position),
            ("Ada", "Lovelace", "ada@acme.test", "+1 555", "CTO"),
        )
        self.assertEqual(new.booking_code, "Speaker")
        self.assertEqual(new.delegate_number, 2)
        self.assertEqual(str(new.discount), "0.20")
        self.assertEqual(new.add_ons, "Workshop")

    def test_attendance_starts_not_in_on_the_new_event(self):
        """The delegate attended nothing yet — a new event has its own door."""
        self.transfer()
        new = BookDelegate.objects.get(invoice__invoice_number="DST-1")
        self.assertEqual(new.attendance, BookDelegate.Attendance.PENDING)

    def test_the_new_invoice_takes_the_target_events_edition_and_owner(self):
        self.transfer()
        dest = BookEvent.objects.get(invoice_number="DST-1")
        # 2026 from the target event's date — NOT 2025 from the source. An unset
        # edition would drop the booking out of every per-edition report.
        self.assertEqual(dest.edition, 2026)
        self.assertEqual(dest.event_code, DST_CODE)
        self.assertEqual(dest.sales_executive_id, self.rep.id)
        self.assertEqual(dest.source, BookEvent.Source.MANUAL)

    def test_the_new_invoice_carries_the_payment_details_forward(self):
        self.transfer()
        dest = BookEvent.objects.get(invoice_number="DST-1")
        self.assertEqual(dest.ticket_tier, "SEB")
        self.assertEqual(dest.payment_type, "Bank")
        self.assertEqual(dest.paid_or_free, "Paid")
        self.assertEqual(dest.company_name, "Acme Ltd")
        self.assertEqual(dest.booking_code, "Speaker")

    def test_the_reference_breadcrumbs_match_the_existing_convention(self):
        self.transfer()
        self.delegate.refresh_from_db()
        new = BookDelegate.objects.get(invoice__invoice_number="DST-1")
        # Appended, not overwritten: the bank reference is what proves the credit.
        self.assertEqual(
            self.delegate.reference,
            "OC250722019137000 / Transferred to XFR - BB'26",
        )
        self.assertEqual(new.reference, "Transferred from - XFR - AA25")

    def test_a_second_transfer_of_the_same_person_flips_the_middle_row(self):
        """The four-hop chain in the live data: each hop out becomes Credit Transferred."""
        self.transfer()
        middle = BookDelegate.objects.get(invoice__invoice_number="DST-1")
        third_event = Event.objects.create(
            event_code="XFR - CC", official_event_name="Third Event",
            event_date="2027-01-20",
        )
        resp = self.transfer(
            delegate=middle, target_event_code=third_event.event_code,
            invoice_number="DST-2",
        )
        self.assertEqual(resp.status_code, 201, resp.content)

        middle.refresh_from_db()
        self.assertEqual(middle.invoice.payment_status, "Credit Transferred")
        self.assertEqual(
            BookEvent.objects.get(invoice_number="DST-2").payment_status,
            "Paid (Transferred)",
        )

    def test_it_logs_who_transferred_what(self):
        from accounts.models import ActionLog
        self.transfer()
        log = ActionLog.objects.filter(action__startswith="Transferred delegate").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.user_id, self.user.id)
        self.assertIn("DST-1", log.details)


class PartialTransferTests(TransferTestBase):
    """
    Only the row clicked moves. With siblings still on the invoice, the status has to
    be a per-delegate override — writing the invoice would relabel their bookings too.
    """

    def setUp(self):
        super().setUp()
        self.sibling = BookDelegate.objects.create(
            invoice=self.invoice, event_code=SRC_CODE, edition=2025,
            first_name="Grace", last_name="Hopper", email="grace@acme.test",
            booking_code="Delegate",
        )

    def test_the_invoice_status_is_left_alone(self):
        resp = self.transfer()
        self.assertEqual(resp.status_code, 201, resp.content)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, "Paid")
        self.assertEqual(resp.data["source"]["scope"], "delegate")

    def test_the_moved_row_carries_the_status_as_an_override(self):
        self.transfer()
        self.delegate.refresh_from_db()
        self.assertEqual(self.delegate.delegate_payment_status, "Credit Transferred")

    def test_the_sibling_is_untouched(self):
        self.transfer()
        self.sibling.refresh_from_db()
        self.assertIsNone(self.sibling.delegate_payment_status)
        self.assertEqual(self.sibling.reference, "")
        self.assertEqual(self.sibling.invoice.event_code, SRC_CODE)

    def test_only_one_delegate_lands_on_the_new_invoice(self):
        self.transfer()
        dest = BookEvent.objects.get(invoice_number="DST-1")
        self.assertEqual(
            list(dest.delegates.values_list("email", flat=True)), ["ada@acme.test"])


class TransferInvoiceNumberTests(TransferTestBase):

    def test_an_existing_number_on_the_target_event_is_reused(self):
        """How a second delegate joins a transfer already made."""
        existing = BookEvent.objects.create(
            invoice_number="DST-1", event_code=DST_CODE, edition=2026,
            payment_status="Paid (Transferred)",
        )
        resp = self.transfer()
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(resp.data["created"]["reused_invoice"])
        self.assertEqual(existing.delegates.count(), 1)
        self.assertEqual(BookEvent.objects.filter(invoice_number="DST-1").count(), 1)

    def test_reusing_a_non_transfer_invoice_still_lands_as_paid_transferred(self):
        """
        The modal promises the new row reads Paid (Transferred). Reusing an invoice
        that says something else must not quietly break that promise — and must not
        relabel the delegates already booked against that invoice either.
        """
        other = BookEvent.objects.create(
            invoice_number="DST-1", event_code=DST_CODE, edition=2026,
            payment_status="Pending",
        )
        sitting_there = BookDelegate.objects.create(
            invoice=other, event_code=DST_CODE, edition=2026,
            first_name="Alan", last_name="Turing", email="alan@acme.test",
        )
        resp = self.transfer()
        self.assertEqual(resp.status_code, 201, resp.content)

        new = other.delegates.get(email="ada@acme.test")
        self.assertEqual(new.delegate_payment_status, "Paid (Transferred)")
        other.refresh_from_db()
        sitting_there.refresh_from_db()
        self.assertEqual(other.payment_status, "Pending")
        self.assertIsNone(sitting_there.delegate_payment_status)

    def test_reusing_a_transfer_invoice_leaves_the_status_on_the_invoice(self):
        BookEvent.objects.create(
            invoice_number="DST-1", event_code=DST_CODE, edition=2026,
            payment_status="Paid (Transferred)",
        )
        self.transfer()
        new = BookDelegate.objects.get(invoice__invoice_number="DST-1",
                                       email="ada@acme.test")
        self.assertIsNone(
            new.delegate_payment_status,
            "an override here would duplicate a status the invoice already carries",
        )

    def test_an_existing_number_on_another_event_is_refused(self):
        BookEvent.objects.create(
            invoice_number="DST-1", event_code=SRC_CODE, edition=2025,
        )
        resp = self.transfer()
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertIn("already exists", resp.data["detail"])
        # Nothing may have been written on the way to the refusal.
        self.delegate.refresh_from_db()
        self.assertIsNone(self.delegate.delegate_payment_status)
        self.assertEqual(self.delegate.reference, "OC250722019137000")

    def test_transferring_the_same_person_onto_the_same_invoice_twice_is_refused(self):
        self.assertEqual(self.transfer().status_code, 201)
        resp = self.transfer()
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertIn("already on invoice", resp.data["detail"])
        self.assertEqual(
            BookDelegate.objects.filter(email="ada@acme.test").count(), 2,
            "the refused second attempt must not have created a third row",
        )

    def test_a_missing_invoice_number_is_refused(self):
        resp = self.transfer(invoice_number="")
        self.assertEqual(resp.status_code, 400, resp.content)


class TransferTargetValidationTests(TransferTestBase):

    def test_an_unknown_event_code_is_refused(self):
        resp = self.transfer(target_event_code="NOPE - ZZ")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("No event with code", resp.data["detail"])

    def test_transferring_to_the_same_event_is_refused(self):
        resp = self.transfer(target_event_code=SRC_CODE)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("already on", resp.data["detail"])

    def test_a_missing_target_is_refused(self):
        resp = self.transfer(target_event_code="")
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_a_refusal_creates_no_booking(self):
        self.transfer(target_event_code="NOPE - ZZ")
        self.assertEqual(BookEvent.objects.filter(event_code="NOPE - ZZ").count(), 0)
        self.assertEqual(BookDelegate.objects.count(), 1)


class TransferPermissionTests(TransferTestBase):
    """
    A transfer needs create AND update on bookings. The permission class can only
    map the POST to one of them, so the other is asserted in the view — this is the
    caller that proves it, holding create but not update.
    """

    def setUp(self):
        super().setUp()
        self.role = Team.objects.create(
            name="xfr_create_only",
        )
        TeamPermission.objects.create(
            team=self.role, module="bookings",
            can_view=True, can_create=True, can_update=False, can_delete=False,
        )
        self.create_only = User.objects.create_user(
            username="xfr_create", password="x", role="sales", email="xc@iq-hub.com",
        )
        self.create_only.team = self.role
        self.create_only.save()
        self.create_only.assigned_events.add(self.src_event, self.dst_event)

    def test_create_without_update_cannot_transfer(self):
        resp = self.transfer(user=self.create_only)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(BookEvent.objects.filter(invoice_number="DST-1").count(), 0)

    def test_update_and_create_together_can(self):
        perm = self.role.permissions.get(module="bookings")
        perm.can_update = True
        perm.save()
        resp = self.transfer(user=self.create_only)
        self.assertEqual(resp.status_code, 201, resp.content)
