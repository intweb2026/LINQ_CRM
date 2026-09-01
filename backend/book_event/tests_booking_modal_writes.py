"""
book_event/tests_booking_modal_writes.py
─────────────────────────────────────────
The invoice write the Bookings tab's edit/new modal performs, end to end.

FOUR SILENT FAILURES THIS LOCKS DOWN
Each one succeeded at the HTTP level and changed nothing (or changed the wrong
thing), which is why none of them surfaced as an error anywhere:

  1. booking_code and delegate_number were absent from BookEventDetailSerializer's
     _ALLOWED_DELEGATE set, so the nested delegate payload had them filtered out
     before the write. 200 OK, edit discarded.
  2. booking_code lived only on the invoice, so two delegates on one invoice could
     not hold different codes — the last one written won for all of them.
  3. Nothing outside the website-intake path set invoice.sales_executive, so a
     booking entered by hand had no owner, and one transferred to another event
     kept the previous event's owner.
  4. "IQ Staff" was not a declared payment status, so it fails choice validation
     on every path that runs full_clean() — the mass-update engine included.

The suite drives the REAL viewset through APIRequestFactory rather than calling
serializers directly: _ALLOWED_DELEGATE filtering, the RBAC mixin and the
permission class are all part of what has to work for an edit to stick.

    python manage.py test book_event.tests_booking_modal_writes
"""
from datetime import date

from django.db.models.functions import Coalesce

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate


from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event
from teams.models import Team

User = get_user_model()

EVENT_A = "MODAL - AA"
EVENT_B = "MODAL - BB"


def _view(method_map):
    from book_event.views import BookEventViewSet
    return BookEventViewSet.as_view(method_map)


def make_event(code, sales_executive=None):
    """
    Event.save() DERIVES `name` from official_event_name and syncs sales_team from
    the sales_executive FK, so the fixture sets the FK and lets the model fill the
    text field — the same way the Events tab does.
    """
    return Event.objects.create(
        event_code=code, official_event_name=f"{code} Conference",
        event_date="2026-06-01", sales_executive=sales_executive,
    )


class BookingModalWriteTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(
            name="modal_admin", is_all_access=True,
        )
        cls.admin = User.objects.create_user(
            username="modal_admin_u", password="x", role="admin", email="ma@iq-hub.com",
        )
        cls.admin.team = cls.role
        cls.admin.save()

        cls.rep_a = User.objects.create_user(
            username="rep_a", password="x", role="sales", email="rep_a@iq-hub.com",
            first_name="Rep", last_name="Aye",
        )
        cls.rep_b = User.objects.create_user(
            username="rep_b", password="x", role="sales", email="rep_b@iq-hub.com",
            first_name="Rep", last_name="Bee",
        )

    def setUp(self):
        self.factory = APIRequestFactory()
        self.event_a = make_event(EVENT_A, sales_executive=self.rep_a)
        self.event_b = make_event(EVENT_B, sales_executive=self.rep_b)

    # ── helpers ─────────────────────────────────────────────────────────────
    def patch_invoice(self, invoice, body):
        req = self.factory.patch(f"/api/invoices/{invoice.pk}/", body, format="json")
        force_authenticate(req, user=self.admin)
        resp = _view({"patch": "partial_update"})(req, pk=invoice.pk)
        resp.render()
        return resp

    def post_invoice(self, body):
        req = self.factory.post("/api/invoices/", body, format="json")
        force_authenticate(req, user=self.admin)
        resp = _view({"post": "create"})(req)
        resp.render()
        return resp

    def make_invoice(self, **over):
        kwargs = {
            "invoice_number": "MOD-1", "event_code": EVENT_A,
            "payment_status": "Pending", "booking_code": "Delegate",
        }
        kwargs.update(over)
        return BookEvent.objects.create(**kwargs)

    def make_delegate(self, invoice, email="one@acme.test", **over):
        kwargs = {
            "invoice": invoice, "event_code": invoice.event_code,
            "first_name": "One", "last_name": "Delegate", "email": email,
        }
        kwargs.update(over)
        return BookDelegate.objects.create(**kwargs)

    # ── 1. booking_code and delegate_number actually persist ────────────────
    def test_delegate_booking_code_and_number_are_saved(self):
        inv = self.make_invoice()
        d = self.make_delegate(inv)

        resp = self.patch_invoice(inv, {
            "delegates": [{
                "id": d.id, "first_name": "One", "last_name": "Delegate",
                "email": d.email, "booking_code": "Speaker Table",
                "delegate_number": 3,
            }],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        d.refresh_from_db()
        self.assertEqual(d.booking_code, "Speaker Table")
        self.assertEqual(d.delegate_number, 3)

    def test_two_delegates_on_one_invoice_keep_different_booking_codes(self):
        """The reason the column moved onto the delegate at all."""
        inv = self.make_invoice()
        d1 = self.make_delegate(inv, email="a@acme.test")
        d2 = self.make_delegate(inv, email="b@acme.test", first_name="Two")

        resp = self.patch_invoice(inv, {
            "delegates": [
                {"id": d1.id, "email": d1.email, "first_name": "One", "booking_code": "Speaker"},
                {"id": d2.id, "email": d2.email, "first_name": "Two", "booking_code": "Group Pass"},
            ],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        d1.refresh_from_db()
        d2.refresh_from_db()
        self.assertEqual((d1.booking_code, d2.booking_code), ("Speaker", "Group Pass"))

    def test_blank_delegate_booking_code_inherits_the_invoice(self):
        """
        Website intake sets booking_code on the invoice only. The delegate column is
        what the table now reads, so a row created without one must not read blank.
        """
        inv = self.make_invoice(booking_code="Media")
        d = self.make_delegate(inv)
        self.assertEqual(d.booking_code, "Media")

    def test_an_explicit_delegate_code_is_not_overwritten_by_the_invoice(self):
        inv = self.make_invoice(booking_code="Media")
        d = self.make_delegate(inv, booking_code="SPP")
        d.save()
        d.refresh_from_db()
        self.assertEqual(d.booking_code, "SPP")

    # ── 1b. The delegate's company survives the write ───────────────────────
    # Delegate Company is a REQUIRED column in both booking modals, and
    # company_name_raw was in NEITHER allow-list. The value was filtered out here
    # after surviving the trip from the browser, so every hand-entered booking
    # stored a blank company under a form that would not submit without one — and
    # company_name_raw is what the Bookings tab displays (company_display) and
    # searches on.
    def test_create_saves_the_delegate_company(self):
        resp = self.post_invoice({
            "invoice_number": "MOD-CO", "event_code": EVENT_A,
            "delegates": [{"first_name": "New", "last_name": "Person",
                           "email": "co@acme.test", "company_name_raw": "Acme Ltd"}],
        })
        self.assertEqual(resp.status_code, 201, resp.content)

        d = BookDelegate.objects.get(email="co@acme.test")
        self.assertEqual(d.company_name_raw, "Acme Ltd")
        # The column the table actually renders.
        self.assertEqual(d.company_display, "Acme Ltd")

    def test_patch_saves_the_delegate_company(self):
        inv = self.make_invoice()
        d = self.make_delegate(inv)

        resp = self.patch_invoice(inv, {
            "delegates": [{"id": d.id, "email": d.email, "first_name": "One",
                           "company_name_raw": "Globex"}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        d.refresh_from_db()
        self.assertEqual(d.company_name_raw, "Globex")

    def test_create_and_update_accept_the_same_delegate_fields(self):
        """
        The allow-list is ONE constant now. It was two copies, and a key present in
        only one is invisible from outside: both requests answer 200/201 either
        way, and only the value the user typed is missing afterwards.
        """
        import inspect
        from book_event import serializers as ser

        source = inspect.getsource(ser)
        self.assertNotIn(
            "_ALLOWED_DELEGATE = {", source,
            "_ALLOWED_DELEGATE has been re-declared inside a method — the two "
            "copies are exactly what let booking_code and company_name_raw go "
            "missing from one write path but not the other.",
        )
        for field in ("company_name_raw", "booking_code", "delegate_number"):
            self.assertIn(field, ser._ALLOWED_DELEGATE)

    # ── 1c. The 400 the Bookings modal used to hide ─────────────────────────
    def test_a_delegate_email_without_an_at_sign_is_rejected_by_name(self):
        """
        POST /api/invoices/ 400 51 — the report that started this.

        The modal posted "harrison" because it only tested that the email cell was
        non-empty, and then replaced the server's answer with "check the form and
        try again". The message has to keep NAMING THE ROW: the frontend now shows
        it verbatim (api/client.js apiErrorMessage), and a delegate index is the
        only thing that tells the user which of ten rows to fix.
        """
        resp = self.post_invoice({
            "invoice_number": "MOD-BADMAIL", "event_code": EVENT_A,
            "delegates": [
                {"first_name": "Fine", "email": "fine@acme.test"},
                {"first_name": "Bad", "email": "harrison"},
            ],
        })
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("Delegate #2", str(resp.data["delegates"][0]))
        self.assertFalse(BookEvent.objects.filter(invoice_number="MOD-BADMAIL").exists())

    # ── 2. A PATCH must leave unnamed columns alone ─────────────────────────
    def test_patch_that_names_only_delegates_leaves_the_invoice_intact(self):
        """
        The counterpart to the frontend fix: the API layer no longer pads the body
        with defaults, and the server must not invent them either.
        """
        inv = self.make_invoice(
            payment_status="Paid", booking_code="Speaker", company_name="Acme Ltd",
            request_date="2026-02-03", source=BookEvent.Source.WEBSITE,
        )
        d = self.make_delegate(inv)

        resp = self.patch_invoice(inv, {
            "delegates": [{"id": d.id, "email": d.email, "first_name": "One",
                           "booking_code": "Speaker"}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        inv.refresh_from_db()
        self.assertEqual(inv.payment_status, "Paid")
        self.assertEqual(inv.booking_code, "Speaker")
        self.assertEqual(inv.company_name, "Acme Ltd")
        self.assertEqual(str(inv.request_date), "2026-02-03")
        self.assertEqual(inv.source, BookEvent.Source.WEBSITE)

    # ── 3. Sales executive comes from the event ─────────────────────────────
    def test_create_takes_the_sales_executive_from_the_event(self):
        resp = self.post_invoice({
            "invoice_number": "MOD-NEW", "event_code": EVENT_A,
            "delegates": [{"first_name": "New", "last_name": "Person",
                           "email": "new@acme.test"}],
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        inv = BookEvent.objects.get(invoice_number="MOD-NEW")
        self.assertEqual(inv.sales_executive_id, self.rep_a.id)

    def test_transfer_to_another_event_re_homes_the_booking(self):
        inv = self.make_invoice()
        inv.sales_executive = self.rep_a
        inv.save()
        d = self.make_delegate(inv)

        resp = self.patch_invoice(inv, {
            "event_code": EVENT_B,
            "delegates": [{"id": d.id, "email": d.email, "first_name": "One"}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        inv.refresh_from_db()
        self.assertEqual(inv.event_code, EVENT_B)
        self.assertEqual(inv.sales_executive_id, self.rep_b.id)
        # The delegates move with the invoice — the row would otherwise still be
        # counted against the old event.
        d.refresh_from_db()
        self.assertEqual(d.event_code, EVENT_B)

    def test_a_save_that_does_not_change_the_event_keeps_the_current_owner(self):
        """
        A deliberate per-invoice owner must survive an unrelated edit. Re-deriving
        on every save would quietly undo it.
        """
        inv = self.make_invoice()
        inv.sales_executive = self.rep_b          # not event A's executive
        inv.save()
        d = self.make_delegate(inv)

        resp = self.patch_invoice(inv, {
            "event_code": EVENT_A,                # unchanged
            "delegates": [{"id": d.id, "email": d.email, "first_name": "One"}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        inv.refresh_from_db()
        self.assertEqual(inv.sales_executive_id, self.rep_b.id)

    def test_an_explicit_sales_executive_in_the_payload_wins(self):
        resp = self.post_invoice({
            "invoice_number": "MOD-PINNED", "event_code": EVENT_A,
            "sales_executive": self.rep_b.id,
            "delegates": [{"first_name": "P", "email": "p@acme.test"}],
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        inv = BookEvent.objects.get(invoice_number="MOD-PINNED")
        self.assertEqual(inv.sales_executive_id, self.rep_b.id)

    def test_an_event_with_no_executive_leaves_the_booking_unowned(self):
        """Unassigned is a legitimate answer, not an error."""
        make_event("MODAL - CC")
        resp = self.post_invoice({
            "invoice_number": "MOD-NOEXEC", "event_code": "MODAL - CC",
            "delegates": [{"first_name": "N", "email": "n@acme.test"}],
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertIsNone(
            BookEvent.objects.get(invoice_number="MOD-NOEXEC").sales_executive_id
        )


class SalesExecutiveResolutionTests(TestCase):
    """
    auto_assign_sales' precedence, which every booking-creating path shares.

    The Events tab's FK is consulted first; the older assigned_events m2m remains
    the fallback so events maintained only through it resolve as they always did.
    """

    @classmethod
    def setUpTestData(cls):
        cls.from_fk = User.objects.create_user(
            username="sr_fk", password="x", role="sales", email="sr1@iq-hub.com",
        )
        cls.from_m2m = User.objects.create_user(
            username="sr_m2m", password="x", role="sales", email="sr2@iq-hub.com",
        )

    def test_the_events_tab_fk_wins(self):
        event = make_event("RESOLVE - AA", sales_executive=self.from_fk)
        self.from_m2m.assigned_events.add(event)
        self.assertEqual(BookEvent.auto_assign_sales("RESOLVE - AA"), self.from_fk)

    def test_the_assigned_events_m2m_is_the_fallback(self):
        event = make_event("RESOLVE - BB")
        self.from_m2m.assigned_events.add(event)
        self.assertEqual(BookEvent.auto_assign_sales("RESOLVE - BB"), self.from_m2m)

    def test_an_unknown_event_code_resolves_to_none(self):
        self.assertIsNone(BookEvent.auto_assign_sales("NOSUCH - ZZ"))


class PaymentStatusChoiceTests(TestCase):
    """'IQ Staff' has to be a declared choice, not merely a string that fits."""

    def test_iq_staff_is_a_declared_payment_status(self):
        self.assertIn("IQ Staff", BookEvent.PaymentStatus.values)

    def test_iq_staff_passes_model_validation(self):
        inv = BookEvent(invoice_number="IQ-1", event_code="IQ - AA",
                        payment_status="IQ Staff")
        inv.full_clean(exclude=["sales_executive", "team_leader", "updated_by"])

    def test_the_statuses_the_bookings_tab_no_longer_offers_are_still_accepted(self):
        """
        'Unpaid' and 'Free' were dropped from the UI list, not from the model: one
        delegate override in the live data holds 'Free', and a value the model
        refuses cannot be read back through a choice-validated filter.
        """
        for legacy in ("Unpaid", "Free"):
            self.assertIn(legacy, BookEvent.PaymentStatus.values)


class SppClearsTheDatePaidTests(TestCase):
    """
    Booking Code → SPP blanks Date Paid and sets Payable/Free to Free.

    The UI half is DelegateTable.update(); this is the half that made it look
    like nothing happened. effective_payment_date resolves as
    `delegate_payment_date or invoice.payment_date`, so writing the override as
    NULL while the invoice kept its date left the resolved value untouched: the
    cell blanked, the PATCH returned 200, and the old date came back with the
    next refetch. The modal now sends payment_date: null for the INVOICE as soon
    as any delegate has none — api/bookings.js splitPersonLevel — and these lock
    that contract down from the server's side.
    """

    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(name="spp_admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="spp_admin_u", password="x", role="admin", email="spp@iq-hub.com",
        )
        cls.admin.team = cls.role
        cls.admin.save()

    def setUp(self):
        self.factory = APIRequestFactory()
        make_event(EVENT_A)
        self.inv = BookEvent.objects.create(
            invoice_number="SPP-1", event_code=EVENT_A, booking_code="Delegate",
            payment_status="Paid", payment_date="2026-03-01", paid_or_free="Paid",
        )

    def delegate(self, email, **over):
        kwargs = {
            "invoice": self.inv, "event_code": EVENT_A,
            "first_name": "A", "last_name": "Delegate", "email": email,
        }
        kwargs.update(over)
        return BookDelegate.objects.create(**kwargs)

    def patch(self, body):
        req = self.factory.patch(f"/api/invoices/{self.inv.pk}/", body, format="json")
        force_authenticate(req, user=self.admin)
        resp = _view({"patch": "partial_update"})(req, pk=self.inv.pk)
        resp.render()
        return resp

    def resolved(self, delegate):
        from book_delegate.serializers import BookDelegateListSerializer
        delegate.refresh_from_db()
        data = BookDelegateListSerializer(delegate).data
        return data["effective_payment_date"], data["effective_paid_or_free"]

    def test_the_only_delegate_going_spp_clears_the_whole_booking(self):
        d = self.delegate("one@acme.test")

        resp = self.patch({
            # What the modal sends once its one row holds SPP: the invoice's own
            # date NULLed, Free agreed by every delegate so it goes on the invoice.
            "payment_date": None,
            "paid_or_free": "Free",
            "delegates": [{
                "id": d.id, "email": d.email, "first_name": "A",
                "booking_code": "SPP",
                "delegate_payment_date": None, "delegate_paid_or_free": None,
            }],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        self.inv.refresh_from_db()
        self.assertIsNone(self.inv.payment_date)
        self.assertEqual(self.inv.paid_or_free, "Free")
        d.refresh_from_db()
        self.assertEqual(d.booking_code, "SPP")
        self.assertEqual(self.resolved(d), (None, "Free"))

    def test_one_delegate_of_two_going_spp_leaves_the_other_paid(self):
        """
        The blank is only expressible with the invoice column NULL, so the
        delegate that still HAS a date carries it as an override. Both rows must
        resolve to what the modal was showing.
        """
        spp = self.delegate("spp@acme.test")
        keep = self.delegate("keep@acme.test", first_name="B")

        resp = self.patch({
            "payment_date": None,   # some delegate has none
            "delegates": [
                {"id": spp.id, "email": spp.email, "first_name": "A",
                 "booking_code": "SPP",
                 "delegate_payment_date": None, "delegate_paid_or_free": "Free"},
                {"id": keep.id, "email": keep.email, "first_name": "B",
                 "booking_code": "Delegate",
                 "delegate_payment_date": "2026-03-01", "delegate_paid_or_free": "Paid"},
            ],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        self.inv.refresh_from_db()
        self.assertIsNone(self.inv.payment_date)
        self.assertEqual(self.resolved(spp), (None, "Free"))
        self.assertEqual(self.resolved(keep), ("2026-03-01", "Paid"))


class RequestDateEditTests(TestCase):
    """
    Request Date has to be changeable from the booking modal.

    TWO BUGS THIS SITS BEHIND
    Nobody could edit a Request Date, and the endpoint was never the reason; it
    has always accepted the invoice column. The browser simply never sent it.
    Then, sent as one shared invoice value, editing one delegate's date moved
    every delegate on the invoice with it. Request Date and Invoice Date are now
    a per-delegate OVERRIDE pair like the five payment columns,
    delegate_request_date and delegate_invoice_date, null meaning "inherit the
    invoice". The browser half lives in frontend/src/api/bookings.js
    splitPersonLevel, pinned in api/bookings.invoiceDates.test.js.

    What is pinned HERE is the server end of that contract. The modal's PATCH
    stores an invoice-level date, a null CLEARS it, an override sticks to the
    one delegate that carries it, and booked_on follows in every case. That
    last one is the trap: booked_on is the delegate-level copy of the booking
    date and the column the period window filters on, and the modal's delegate
    branch writes through a queryset .update() that never runs
    BookDelegate.save(). A date stored without it would show one value in the
    Bookings table and be windowed by another.
    """

    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(name="rd_admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="rd_admin_u", password="x", role="admin", email="rd@iq-hub.com",
        )
        cls.admin.team = cls.role
        cls.admin.save()

    def setUp(self):
        self.factory = APIRequestFactory()
        make_event(EVENT_A)
        self.inv = BookEvent.objects.create(
            invoice_number="MOD-RD", event_code=EVENT_A,
            request_date="2026-01-05", invoice_date="2026-01-09",
        )
        self.d = BookDelegate.objects.create(
            invoice=self.inv, event_code=self.inv.event_code,
            first_name="One", last_name="Delegate", email="rd@acme.test",
        )

    def patch(self, body):
        req = self.factory.patch(f"/api/invoices/{self.inv.pk}/", body, format="json")
        force_authenticate(req, user=self.admin)
        resp = _view({"patch": "partial_update"})(req, pk=self.inv.pk)
        resp.render()
        return resp

    def resolved(self, delegate):
        """effective_request_date, i.e. what the Bookings table shows."""
        from book_delegate.serializers import BookDelegateListSerializer
        delegate.refresh_from_db()
        return BookDelegateListSerializer(delegate).data["effective_request_date"]

    def test_the_modal_patch_stores_a_new_request_date(self):
        resp = self.patch({
            "request_date": "2026-02-11",
            "delegates": [{"id": self.d.id, "email": self.d.email, "first_name": "One"}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        self.inv.refresh_from_db()
        self.assertEqual(self.inv.request_date, date(2026, 2, 11))
        # The delegate's own copy of the booking date moves with it.
        self.d.refresh_from_db()
        self.assertEqual(self.d.booked_on, date(2026, 2, 11))

    def test_a_null_request_date_clears_it_and_booked_on_falls_back(self):
        resp = self.patch({
            "request_date": None,
            "delegates": [{"id": self.d.id, "email": self.d.email, "first_name": "One"}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        self.inv.refresh_from_db()
        self.assertIsNone(self.inv.request_date)
        # booked_on is COALESCE(request_date, invoice_date) by another name.
        self.d.refresh_from_db()
        self.assertEqual(self.d.booked_on, date(2026, 1, 9))

    def test_the_invoice_date_is_editable_the_same_way(self):
        resp = self.patch({
            "invoice_date": "2026-04-02",
            "delegates": [{"id": self.d.id, "email": self.d.email, "first_name": "One"}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        self.inv.refresh_from_db()
        self.assertEqual(self.inv.invoice_date, date(2026, 4, 2))

    def test_one_delegate_keeps_its_own_request_date(self):
        """
        The reason the override column exists. Two people on one invoice, and a
        correction to one of them must not move the other.
        """
        other = BookDelegate.objects.create(
            invoice=self.inv, event_code=self.inv.event_code,
            first_name="Two", last_name="Delegate", email="rd2@acme.test",
        )
        resp = self.patch({
            "delegates": [
                {"id": self.d.id, "email": self.d.email, "first_name": "One",
                 "delegate_request_date": "2026-07-07"},
                {"id": other.id, "email": other.email, "first_name": "Two"},
            ],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        self.d.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.d.delegate_request_date, date(2026, 7, 7))
        self.assertIsNone(other.delegate_request_date)
        # And the resolved values, which is what the table shows: the overridden
        # row on its own date, the other still on the invoice's.
        self.assertEqual(self.resolved(self.d), "2026-07-07")
        self.assertEqual(self.resolved(other), "2026-01-05")
        # booked_on follows the override on one row and the invoice on the
        # other, or the period window and the table would disagree.
        self.assertEqual(self.d.booked_on, date(2026, 7, 7))
        self.assertEqual(other.booked_on, date(2026, 1, 5))

    def test_a_delegate_invoice_date_override_is_independent_of_the_request_date(self):
        resp = self.patch({
            "delegates": [{"id": self.d.id, "email": self.d.email, "first_name": "One",
                           "delegate_invoice_date": "2026-09-09"}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        self.d.refresh_from_db()
        self.assertEqual(self.d.delegate_invoice_date, date(2026, 9, 9))
        self.assertIsNone(self.d.delegate_request_date)
        # The invoice's request date still leads, so the booking date is unmoved.
        self.assertEqual(self.d.booked_on, date(2026, 1, 5))

    def test_clearing_an_override_returns_the_delegate_to_the_invoice(self):
        self.d.delegate_request_date = date(2026, 7, 7)
        self.d.save()
        self.assertEqual(self.d.booked_on, date(2026, 7, 7))

        resp = self.patch({
            "delegates": [{"id": self.d.id, "email": self.d.email, "first_name": "One",
                           "delegate_request_date": None}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        self.d.refresh_from_db()
        self.assertIsNone(self.d.delegate_request_date)
        self.assertEqual(self.resolved(self.d), "2026-01-05")
        self.assertEqual(self.d.booked_on, date(2026, 1, 5))

    def test_an_invoice_date_change_does_not_destroy_a_delegate_override(self):
        """
        BookEvent.save() cascades booked_on to every delegate on the invoice. It
        used to write one literal date, which would have overwritten the row
        that carries its own; it writes the COALESCE chain per row instead.
        """
        self.d.delegate_request_date = date(2026, 7, 7)
        self.d.save()
        plain = BookDelegate.objects.create(
            invoice=self.inv, event_code=self.inv.event_code,
            first_name="Two", last_name="Delegate", email="rd2@acme.test",
        )

        resp = self.patch({"request_date": "2026-02-11"})
        self.assertEqual(resp.status_code, 200, resp.content)

        self.d.refresh_from_db()
        plain.refresh_from_db()
        self.assertEqual(self.d.delegate_request_date, date(2026, 7, 7))
        self.assertEqual(self.d.booked_on, date(2026, 7, 7))
        self.assertEqual(plain.booked_on, date(2026, 2, 11))

    def test_the_bookings_list_sorts_by_the_resolved_date(self):
        """
        _sort_request_date is the Request Date column's serverOrdering. It used
        to be F("invoice__request_date"), which orders the table by a value the
        cell is not showing for any row carrying an override; the same reasoning
        as accounts/tests_resolved_ordering.py for the payment fields.

        The two rows here are deliberately ordered one way by the invoices and
        the other way by the resolved dates, so the assertion cannot pass under
        the old expression.
        """
        from book_delegate.views import BookDelegateViewSet

        self.d.delegate_request_date = date(2026, 7, 7)   # invoice says 2026-01-05
        self.d.save()
        later_invoice = BookEvent.objects.create(
            invoice_number="MOD-RD2", event_code=EVENT_A,
            request_date="2026-04-01", invoice_date="2026-04-01",
        )
        other = BookDelegate.objects.create(
            invoice=later_invoice, event_code=later_invoice.event_code,
            first_name="Two", last_name="Delegate", email="rd2@acme.test",
        )

        req = self.factory.get("/api/delegates/", {"ordering": "-_sort_request_date"})
        force_authenticate(req, user=self.admin)
        resp = BookDelegateViewSet.as_view({"get": "list"})(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)

        ids = [r["id"] for r in resp.data["results"]]
        self.assertEqual(ids, [self.d.id, other.id])


class AddedDelegateIsVisibleToTheSaveTests(TestCase):
    """
    A delegate ADDED to a saved booking, and one REMOVED from it, as the invoice
    endpoint reports them back.

    THE FIFTH SILENT FAILURE. Adding a sixth delegate to a five-delegate booking
    wrote the row and then answered the save as if it had not: the response
    listed the original five and `delegate_count` stayed at five, so every
    surface reading either — the modal's own 200, the invoice list, the delegate
    export — agreed that the save had done nothing. The row was in the database
    the whole time, which is what made it look like an intermittent write.

    The cause is the viewset, not the payload. BookEventViewSet.get_queryset()
    loads the instance with prefetch_related("delegates__company") for the update
    actions, and BookEventDetailSerializer.update() then created and deleted rows
    underneath that cache. instance.delegates.count() is a len() of the cached
    list, and to_representation() serialises the same list, so both answered from
    a snapshot taken before the write.

    IT HAS TO RUN THROUGH THE VIEWSET. Calling the serializer directly builds an
    instance with no prefetch cache, so the count and the response are correct by
    accident and the bug is invisible — the prefetch IS the defect.

        python manage.py test book_event.tests_booking_modal_writes
    """

    @classmethod
    def setUpTestData(cls):
        cls.role = Team.objects.create(name="modal_add_admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="modal_add_u", password="x", role="admin", email="mad@iq-hub.com",
        )
        cls.admin.team = cls.role
        cls.admin.save()

    def setUp(self):
        self.factory = APIRequestFactory()
        make_event(EVENT_A)
        self.inv = BookEvent.objects.create(
            invoice_number="MOD-ADD", event_code=EVENT_A,
            payment_status="Pending", booking_code="Delegate",
        )
        self.five = [
            BookDelegate.objects.create(
                invoice=self.inv, event_code=self.inv.event_code,
                first_name=f"P{i}", last_name="Person", email=f"p{i}@acme.test",
            )
            for i in range(1, 6)
        ]
        self.inv.delegate_count = 5
        self.inv.save(update_fields=["delegate_count"])

    def patch(self, delegates):
        body = {"invoice_number": self.inv.invoice_number, "event_code": EVENT_A,
                "delegates": delegates}
        req = self.factory.patch(f"/api/invoices/{self.inv.pk}/", body, format="json")
        force_authenticate(req, user=self.admin)
        resp = _view({"patch": "partial_update"})(req, pk=self.inv.pk)
        resp.render()
        return resp

    @staticmethod
    def _row(d):
        return {"id": d.id, "first_name": d.first_name,
                "last_name": d.last_name, "email": d.email}

    def test_the_sixth_delegate_is_in_the_response_and_the_count(self):
        resp = self.patch(
            [self._row(d) for d in self.five]
            + [{"first_name": "P6", "last_name": "Person", "email": "p6@acme.test"}]
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        # The write itself. This half always passed.
        self.assertEqual(BookDelegate.objects.filter(invoice=self.inv).count(), 6)

        # What the save ANSWERED. This half is the bug.
        self.assertEqual(len(resp.data["delegates"]), 6)
        self.assertIn("p6@acme.test", [d["email"] for d in resp.data["delegates"]])
        self.assertEqual(resp.data["delegate_count"], 6)

        self.inv.refresh_from_db()
        self.assertEqual(self.inv.delegate_count, 6)

    def test_a_removed_delegate_is_gone_from_the_response_and_the_count(self):
        """The same cache, the other direction: a deleted row was echoed back."""
        resp = self.patch([self._row(d) for d in self.five[:4]])
        self.assertEqual(resp.status_code, 200, resp.content)

        self.assertEqual(BookDelegate.objects.filter(invoice=self.inv).count(), 4)
        self.assertEqual(len(resp.data["delegates"]), 4)
        self.assertNotIn("p5@acme.test", [d["email"] for d in resp.data["delegates"]])
        self.assertEqual(resp.data["delegate_count"], 4)

        self.inv.refresh_from_db()
        self.assertEqual(self.inv.delegate_count, 4)
