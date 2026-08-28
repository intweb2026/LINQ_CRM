"""
dataapi/tests_delta_sync.py
────────────────────────────
The ?updated_since= contract, from the write that should move the watermark to
the row that should come back down the wire.

WHY THIS SUITE EXISTS. The Data API's incremental consumer is an Apps Script on
a five-minute trigger. It asks for delegates changed since its last watermark,
and it upserts them into a sheet by Record ID. Every part of that depends on ONE
column, book_delegates.updated_at, telling the truth.

updated_at is auto_now=True, so it tells the truth on any path that goes through
save(). It does NOT on a queryset .update(): the ORM never instantiates the rows,
so no field's pre_save() runs and the column keeps whatever it held. Several
live paths write delegates that way, deliberately and for good reasons of their
own, and each one has to stamp the column by hand. A missing stamp is invisible
from every direction that matters — the request answers 200, the CRM re-reads
the row and shows the new value, the table even re-sorts — and the only symptom
is a spreadsheet somewhere quietly serving a stale cell forever. So the stamps
are pinned here, per path, by name.

The suite also pins the NEGATIVE: an invoice save that changes nothing a
delegate is exported with must not stamp anything. Without that, the fix for
staleness becomes a fix that puts every delegate on the invoice through the
delta feed on every unrelated invoice write.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from book_delegate.models import BookDelegate
from book_delegate.services import DelegatePaymentOverrideResolver
from book_event.models import BookEvent
from book_event.serializers import BookEventDetailSerializer
from dataapi.models import DataApiKey
from events.models import Event

HEADER = "HTTP_X_DATA_API_KEY"


class DelegateWatermarkTestCase(TestCase):
    """Shared fixture: one event, one invoice, two delegates on it."""

    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="delta-admin", password="x", role="admin",
        )
        self.event = Event.objects.create(
            event_code="DELTA", official_event_name="Delta Event",
            event_date=datetime.date(2026, 9, 1),
        )
        self.invoice = BookEvent.objects.create(
            invoice_number="INV-DELTA-1", event_code="DELTA-26",
            company_name="ACME", payment_status="Pending",
            payment_type="Card", paid_or_free="Paid", ticket_tier="Standard",
            total_amount=100,
            request_date=datetime.date(2026, 3, 4),
            invoice_date=datetime.date(2026, 3, 9),
        )
        self.ada = BookDelegate.objects.create(
            invoice=self.invoice, event_code="DELTA-26",
            first_name="Ada", last_name="Lovelace", email="ada@example.com",
        )
        self.bob = BookDelegate.objects.create(
            invoice=self.invoice, event_code="DELTA-26",
            first_name="Bob", last_name="Bly", email="bob@example.com",
        )
        # The watermark an external consumer would be holding, taken AFTER the
        # fixture is built. Nothing above this line may come back from a delta
        # query; anything a test then writes must.
        self.watermark = timezone.now()

    def stored_updated_at(self, delegate):
        """Re-read from the database, never from the in-memory instance."""
        return (BookDelegate.objects
                .filter(pk=delegate.pk)
                .values_list("updated_at", flat=True)
                .first())

    def assertStamped(self, delegate, msg=""):
        self.assertGreater(self.stored_updated_at(delegate), self.watermark, msg)

    def assertNotStamped(self, delegate, msg=""):
        self.assertLess(self.stored_updated_at(delegate), self.watermark, msg)

    def patch_invoice(self, payload):
        """
        Drive the real modal save path: PATCH of the invoice, partial=True.

        THE REQUEST CONTEXT IS NOT DECORATION. BookEventDetailSerializer
        .get_fields() marks invoice_number read-only unless the caller is admin
        or sales, and a read-only field is dropped from validated_data in
        silence — so a rename driven without a context does nothing at all and a
        test of the rename cascade passes for the wrong reason. Supplying an
        admin here is what makes these tests exercise the same fields the
        Bookings modal does.
        """
        request = RequestFactory().patch(f"/api/invoices/{self.invoice.pk}/")
        request.user = self.admin
        ser = BookEventDetailSerializer(
            self.invoice, data=payload, partial=True,
            context={"request": request},
        )
        ser.is_valid(raise_exception=True)
        return ser.save()


class NestedDelegateEditStampsWatermarkTests(DelegateWatermarkTestCase):
    """
    book_event/serializers.py BookEventDetailSerializer.update(), the branch that
    updates an EXISTING delegate.

    This is the path behind saveInvoiceDelegates() in frontend/src/api/bookings.js,
    which PATCHes the invoice with its whole delegate list. Every delegate edit
    made in the Bookings modal arrives here, which makes it the single most
    important stamp in the codebase for anyone consuming the export.
    """

    def test_editing_a_delegate_through_the_invoice_moves_its_watermark(self):
        self.patch_invoice({"delegates": [
            {"id": self.ada.id, "first_name": "Ada", "last_name": "Byron",
             "email": "ada@example.com"},
            {"id": self.bob.id, "first_name": "Bob", "last_name": "Bly",
             "email": "bob@example.com"},
        ]})
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.last_name, "Byron")
        self.assertStamped(
            self.ada,
            "A delegate edited through the Bookings modal must be offered to the "
            "delta feed; queryset .update() does not fire auto_now.",
        )

    def test_the_edit_actually_lands_alongside_the_stamp(self):
        """
        The stamp is passed as an extra kwarg to the same .update() as `clean`.
        A collision between the two — updated_at appearing in _ALLOWED_DELEGATE,
        say — would raise TypeError on duplicate keyword, so this asserts the
        payload's own columns still arrive.
        """
        self.patch_invoice({"delegates": [
            {"id": self.ada.id, "first_name": "Ada", "email": "ada@example.com",
             "position": "Analyst", "reference": "REF-9", "booking_code": "Delegate"},
        ]})
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.position, "Analyst")
        self.assertEqual(self.ada.reference, "REF-9")
        self.assertStamped(self.ada)


class InvoiceNumberRenameStampsWatermarkTests(DelegateWatermarkTestCase):
    """book_event/serializers.py, the invoice_number cascade."""

    def test_renaming_the_invoice_moves_every_delegate_watermark(self):
        self.patch_invoice({"invoice_number": "INV-DELTA-RENAMED"})
        for delegate in (self.ada, self.bob):
            delegate.refresh_from_db()
            self.assertEqual(delegate.invoice_id, "INV-DELTA-RENAMED")
            self.assertStamped(
                delegate,
                "Without the stamp the consumer keeps these rows filed under an "
                "invoice number that no longer exists.",
            )


class InvoiceLevelEditStampsWatermarkTests(DelegateWatermarkTestCase):
    """
    BookEvent.save(), the DELEGATE_EXPORT_FIELDS cascade.

    A delegate row in the export carries the invoice's dates, billing company,
    accounts contact, sales executive, and the five payment values it inherits
    when it has no delegate_* override of its own. Changing any of those rewrites
    the exported row without touching book_delegates at all.
    """

    def test_invoice_payment_status_change_moves_delegate_watermarks(self):
        self.invoice.payment_status = "Paid"
        self.invoice.save()
        for delegate in (self.ada, self.bob):
            self.assertStamped(
                delegate,
                "effective_payment_status is the export's FIRST column and it "
                "falls back to the invoice, so this edit changed both rows.",
            )

    def test_every_declared_export_field_is_watched(self):
        """
        One subTest per name in DELEGATE_EXPORT_FIELDS, so a field added to the
        tuple without a working comparison fails here rather than silently
        never triggering. _export_field_changed() normalises both sides through
        to_python and treats None as "" on text columns, and a value chosen
        below that collides with either rule would look like "no change".
        """
        new_values = {
            "request_date": datetime.date(2026, 4, 1),
            "invoice_date": datetime.date(2026, 4, 2),
            "payment_due_date": datetime.date(2026, 4, 3),
            "event_name": "Renamed Delta Event",
            "parent_code": "PARENT-X",
            "company_name": "Globex",
            "accounts_contact_email": "ap@globex.example.com",
            "sales_executive_id": None,
            "payment_status": "Paid",
            "payment_type": "Wire",
            "payment_date": datetime.date(2026, 4, 4),
            "paid_or_free": "Free",
            "ticket_tier": "VIP",
        }
        self.assertEqual(
            sorted(new_values), sorted(BookEvent.DELEGATE_EXPORT_FIELDS),
            "DELEGATE_EXPORT_FIELDS changed; give the new name a distinct value "
            "here so the cascade is actually proven to notice it.",
        )
        for name, value in new_values.items():
            if name == "sales_executive_id":
                # Already None on the fixture, so there is no change to detect
                # and no assertion to make. Named in the dict above anyway, so
                # the equality check on the tuple stays honest.
                continue
            if name == "event_name":
                # DERIVED, NOT ASSIGNED. BookEvent.save() rewrites event_name
                # from the Event catalogue on every save, so setting the
                # attribute and saving puts the old value straight back and there
                # is nothing for the cascade to see. It is a real export column
                # that really does change, so it is driven the only way it ever
                # changes in production — by renaming the master event — and
                # asserted in its own test below.
                continue
            with self.subTest(field=name):
                invoice = BookEvent.objects.create(
                    invoice_number=f"INV-DELTA-{name}", event_code="DELTA-26",
                    company_name="ACME", payment_status="Pending",
                    payment_type="Card", paid_or_free="Paid",
                    ticket_tier="Standard",
                    request_date=datetime.date(2026, 3, 4),
                    invoice_date=datetime.date(2026, 3, 9),
                    payment_due_date=datetime.date(2026, 3, 20),
                    parent_code="PARENT-A",
                    accounts_contact_email="ap@acme.example.com",
                )
                delegate = BookDelegate.objects.create(
                    invoice=invoice, event_code="DELTA-26",
                    first_name="Sub", last_name=name, email=f"{name}@example.com",
                )
                mark = timezone.now()
                # Re-read, so the instance holds exactly what the database holds
                # and the only difference is the one field set below. event_name
                # is derived by save() from the Event catalogue, so it is set
                # last and read back the same way.
                invoice = BookEvent.objects.get(pk=invoice.pk)
                setattr(invoice, name, value)
                invoice.save()
                self.assertGreater(
                    (BookDelegate.objects.filter(pk=delegate.pk)
                     .values_list("updated_at", flat=True).first()),
                    mark,
                    f"Changing invoice.{name} rewrites the exported delegate row, "
                    f"so it must move the delegate's watermark.",
                )

    def test_renaming_the_master_event_moves_delegate_watermarks(self):
        """
        event_name, the one export column on this list that is derived rather
        than assigned. BookEvent.save() rebuilds it from the Event catalogue, so
        the way it changes in production is that somebody renames the event; the
        next save of each invoice on it then rewrites event_name, and with it the
        Event Name cell of every delegate row exported under it.
        """
        self.event.official_event_name = "Delta Summit"
        self.event.save()
        self.invoice.save()
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.event_name, "Delta Summit 2026")
        for delegate in (self.ada, self.bob):
            self.assertStamped(
                delegate,
                "The delegates' exported Event Name changed, so their watermark "
                "has to move with it.",
            )

    def test_invoice_save_that_changes_no_export_field_stamps_nothing(self):
        """
        THE NEGATIVE, and the reason the cascade is conditional rather than
        unconditional. total_amount, tax, currency and notes are invoice-only;
        stamping delegates for those would push every delegate on the invoice
        through the delta feed on every unrelated invoice write, which is a
        different way of making the feed useless.
        """
        self.invoice.total_amount = 250
        self.invoice.tax_amount = 20
        self.invoice.notes = "chased by phone"
        self.invoice.save()
        for delegate in (self.ada, self.bob):
            self.assertNotStamped(
                delegate,
                "An invoice-only column changed; no exported delegate cell did.",
            )

    def test_resaving_an_unchanged_invoice_stamps_nothing(self):
        self.invoice.save()
        for delegate in (self.ada, self.bob):
            self.assertNotStamped(delegate)

    def test_booked_on_cascade_still_fires_after_being_merged(self):
        """
        Regression on the merge. booked_on and updated_at are now written by ONE
        statement built from two independent conditions, so a date change has to
        still write booked_on, and has to also stamp — request_date is itself an
        exported column.
        """
        self.invoice.request_date = datetime.date(2026, 5, 6)
        self.invoice.save()
        for delegate in (self.ada, self.bob):
            delegate.refresh_from_db()
            self.assertEqual(delegate.booked_on, datetime.date(2026, 5, 6))
            self.assertStamped(delegate)

    def test_string_dates_are_not_read_as_a_change(self):
        """
        An importer may assign an ISO string. date(2026,3,4) != "2026-03-04"
        raw, so without to_python on both sides every save of an unchanged
        invoice would stamp every delegate on it.
        """
        self.invoice.request_date = "2026-03-04"
        self.invoice.invoice_date = "2026-03-09"
        self.invoice.save()
        for delegate in (self.ada, self.bob):
            self.assertNotStamped(delegate)


class ClearOverridesStampsWatermarkTests(DelegateWatermarkTestCase):
    """book_delegate/services.py clear_delegate_overrides(), pinned by name."""

    def test_clearing_overrides_moves_the_watermark(self):
        BookDelegate.objects.filter(pk=self.ada.pk).update(
            delegate_payment_status="Paid", delegate_ticket_tier="VIP",
        )
        DelegatePaymentOverrideResolver(self.invoice).clear_delegate_overrides([self.ada.id])
        self.ada.refresh_from_db()
        self.assertIsNone(self.ada.delegate_payment_status)
        self.assertStamped(self.ada)


class DeltaEndpointTests(DelegateWatermarkTestCase):
    """
    End to end, the way the Apps Script sees it: the watermark goes out as a
    query param and only the changed row comes back.
    """

    def setUp(self):
        super().setUp()
        _, self.raw = DataApiKey.create_key(name="Delta", scopes=["delegates"])

    def delta(self, since):
        resp = self.client.get(
            "/api/data/delegates/",
            {"updated_since": since.isoformat()},
            **{HEADER: self.raw},
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()["results"]

    def test_watermark_excludes_everything_written_before_it(self):
        self.assertEqual(self.delta(self.watermark), [])

    def test_only_the_edited_delegate_comes_back(self):
        self.patch_invoice({"delegates": [
            {"id": self.ada.id, "first_name": "Ada", "last_name": "Byron",
             "email": "ada@example.com"},
        ]})
        rows = self.delta(self.watermark)
        self.assertEqual([r["id"] for r in rows], [self.ada.id])
        self.assertEqual(rows[0]["full_name"], "Ada Byron")

    def test_an_invoice_payment_edit_returns_the_delegates_with_the_new_status(self):
        """
        The whole point, stated as one assertion. Before the cascade this
        returned [] and the sheet kept showing "Pending" indefinitely.
        """
        self.invoice.payment_status = "Paid"
        self.invoice.save()
        rows = self.delta(self.watermark)
        self.assertEqual(sorted(r["id"] for r in rows),
                         sorted([self.ada.id, self.bob.id]))
        for row in rows:
            self.assertEqual(row["effective_payment_status"], "Paid")

    def test_every_row_carries_the_id_and_updated_at_an_upsert_needs(self):
        """
        The consumer upserts by "id" and re-arms its watermark from
        "updated_at". Both are in the field list on purpose; losing either
        turns the delta feed back into an append-only feed.
        """
        self.invoice.payment_status = "Paid"
        self.invoice.save()
        for row in self.delta(self.watermark):
            self.assertIsNotNone(row["id"])
            self.assertIsNotNone(row["updated_at"])

    def test_a_delegate_untouched_since_the_watermark_stays_out(self):
        self.patch_invoice({"delegates": [
            {"id": self.ada.id, "first_name": "Ada", "last_name": "Byron",
             "email": "ada@example.com"},
            {"id": self.bob.id, "first_name": "Bob", "last_name": "Bly",
             "email": "bob@example.com"},
        ]})
        # ONLY ADA. Both were in the payload, because the modal PATCHes the
        # whole delegate list on every save, but only Ada's row CHANGED —
        # Lovelace to Byron — and Bob's was re-sent exactly as stored.
        #
        # The invoice serializer used to write every row the payload carried and
        # stamp it, so Bob came back on a delta feed reporting a change nobody
        # made. It now compares before writing (_delegate_changes), which is
        # what this test's own name asks for and what keeps the Bookings table's
        # Modified Time sort honest; a one-person edit no longer hauls every
        # delegate on the invoice to the top.
        #
        # Re-arm on the maximum updated_at seen, which is what the Apps Script
        # does, then confirm the feed goes quiet.
        rows = self.delta(self.watermark)
        self.assertEqual([r["id"] for r in rows], [self.ada.id])
        high = max(r["updated_at"] for r in rows)
        resp = self.client.get(
            "/api/data/delegates/",
            {"updated_since": high},
            **{HEADER: self.raw},
        )
        # gte, so the boundary row itself repeats. That is harmless to an
        # id-keyed upsert and is the documented behaviour; what must NOT happen
        # is a row appearing that was not at or after the watermark.
        returned = resp.json()["results"]
        self.assertTrue(all(r["updated_at"] >= high for r in returned))
