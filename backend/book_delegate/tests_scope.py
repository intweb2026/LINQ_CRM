"""
book_delegate/tests_scope.py
─────────────────────────────
Who can see a booking.

THE BUG THIS LOCKS DOWN
An event belongs to somebody in two unrelated places, and their names are one
character apart. `User.assigned_events` is an M2M whose reverse on Event is
`assigned_users`; `Event.sales_executive` is a separate FK whose reverse on User
is `assigned_events_list`. The Events module resolves ownership as
`Q(assigned_users=user) | Q(sales_executive=user)` (events/views.py), which is
what fills the event dropdown on the New Booking modal. `assigned_event_codes()`
read only the M2M.

On the 2026-06-11 snapshot the M2M is empty on all 217 events and all 45 users,
while sales_executive is set on most of the catalogue. RBACMixin.rbac_filter
turns an empty code list into `qs.none()`, so the Bookings page was empty for all
42 non-admin accounts: a sales executive could be offered `BIC - PM` in the
dropdown, file a booking against it, and then not see the row they had just
created. Reported as "Terry sees 0 bookings on an event that is theirs".

The delegate list had a second, independent hole. BookDelegate has no
sales_executive column, it reaches one through `invoice__sales_executive`, and
rbac_filter only looked for a field on the model itself. So the invoice list
granted "you sold this" and the delegate list did not, and two people could see
an invoice with none of the delegates on it.

    python manage.py test book_delegate.tests_scope
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.permissions import RBACMixin
from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event

User = get_user_model()


class _Scope(RBACMixin):
    """rbac_filter reads self.request.user and nothing else."""

    def __init__(self, user):
        self.request = type("R", (), {"user": user})()

    def delegates(self):
        return self.rbac_filter_invoice(BookDelegate.objects.all())

    def invoices(self):
        return self.rbac_filter(BookEvent.objects.all())


def _booking(number, event_code, seller=None):
    invoice = BookEvent.objects.create(
        invoice_number=number, event_code=event_code, booking_code="Delegate",
        request_date=date(2026, 6, 1), payment_status="Pending",
        company_name="Acme", total_amount=1000, sales_executive=seller,
    )
    return BookDelegate.objects.create(
        invoice=invoice, event_code=event_code, booking_code="Delegate",
        first_name="Del", last_name=number, email=f"{number}@example.com",
    )


class BookingScopeTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="bs_owner", password="x", role="sales", email="bs1@iq-hub.com")
        cls.stranger = User.objects.create_user(
            username="bs_stranger", password="x", role="sales", email="bs2@iq-hub.com")
        cls.admin = User.objects.create_user(
            username="bs_admin", password="x", role="admin", email="bs3@iq-hub.com")

        # Ownership through the catalogue ONLY, which is the state of every row
        # in the real database. No assigned_events anywhere in this fixture.
        cls.event = Event.objects.create(
            event_code="BIC - PM", name="Owned Event",
            event_date=date(2026, 9, 1), sales_executive=cls.owner)
        cls.other = Event.objects.create(
            event_code="XYZ - QQ", name="Someone Else's",
            event_date=date(2026, 9, 2))

        cls.mine = _booking("INV-MINE", "BIC - PM")
        cls.theirs = _booking("INV-THEIRS", "XYZ - QQ")

    def test_the_events_sales_executive_sees_its_bookings(self):
        """THE REPORTED BUG. No assigned_events row exists, and none is needed."""
        self.assertFalse(self.owner.assigned_events.exists())
        self.assertEqual(self.owner.assigned_event_codes(), ["BIC - PM"])
        self.assertEqual([d.id for d in _Scope(self.owner).delegates()], [self.mine.id])

    def test_a_stranger_sees_nothing(self):
        """Scoping to nothing, never to everything, is the whole point."""
        self.assertEqual(_Scope(self.stranger).delegates().count(), 0)
        self.assertEqual(_Scope(self.stranger).invoices().count(), 0)

    def test_the_m2m_still_grants_access(self):
        """The older mechanism keeps working; this widened it, it replaced nothing."""
        self.stranger.assigned_events.add(self.other)
        self.assertEqual([d.id for d in _Scope(self.stranger).delegates()],
                         [self.theirs.id])

    def test_admin_is_unrestricted(self):
        self.assertIsNone(self.admin.assigned_event_codes())
        self.assertEqual(_Scope(self.admin).delegates().count(), 2)

    def test_selling_an_invoice_shows_you_its_delegates(self):
        """
        The invoice list has always granted "you sold this". The delegate list
        reaches the executive through invoice__sales_executive, so it has to
        grant the same thing or the two disagree on one booking.
        """
        sold = _booking("INV-SOLD", "XYZ - QQ", seller=self.owner)
        scope = _Scope(self.owner)
        # invoice_number, not pk: BookDelegate.invoice points at the number.
        self.assertIn("INV-SOLD", [i.invoice_number for i in scope.invoices()])
        self.assertIn(sold.id, [d.id for d in scope.delegates()])

    def test_a_case_variant_code_still_matches(self):
        """
        Stored codes disagree with the catalogue on case: `Feb2027_BIZ-PM` sits
        against a catalogue row reading `FEB2027_BIZ-PM`, and 9 delegate rows in
        the real data hang on this. An exact match would drop them.
        """
        Event.objects.create(event_code="FEB2027_BIZ-PM", name="Cased",
                             event_date=date(2027, 2, 1), sales_executive=self.owner)
        odd = _booking("INV-CASE", "Feb2027_BIZ-PM")
        self.assertIn(odd.id, [d.id for d in _Scope(self.owner).delegates()])

    def test_a_code_inside_another_code_does_not_leak(self):
        """
        `SFU - AD` is a substring of `BSFU - AD`, both real catalogue rows. Under
        the substring match this filter used to use, holding the first event
        would hand over every booking on the second.
        """
        Event.objects.create(event_code="SFU - AD", name="Short",
                             event_date=date(2026, 10, 1), sales_executive=self.stranger)
        Event.objects.create(event_code="BSFU - AD", name="Long",
                             event_date=date(2026, 10, 2), sales_executive=self.owner)
        short = _booking("INV-SFU", "SFU - AD")
        long_ = _booking("INV-BSFU", "BSFU - AD")

        seen = [d.id for d in _Scope(self.stranger).delegates()]
        self.assertIn(short.id, seen)
        self.assertNotIn(long_.id, seen)

    def test_a_blank_catalogue_code_scopes_to_nothing(self):
        """
        An event with no code would otherwise contribute `event_code = ''` to the
        clause, which matches every booking that never got one.
        """
        Event.objects.create(event_code="", name="Codeless",
                             event_date=date(2026, 11, 1), sales_executive=self.stranger)
        blank = _booking("INV-BLANK", "")
        self.assertEqual(self.stranger.assigned_event_codes(), [])
        self.assertNotIn(blank.id, [d.id for d in _Scope(self.stranger).delegates()])
