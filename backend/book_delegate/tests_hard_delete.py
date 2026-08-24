"""
book_delegate/tests_hard_delete.py
──────────────────────────────────
A delete is a DELETE.

THE REPORT THIS LOCKS DOWN
"I deleted the entries but I still get those entries; they are getting soft
deleted, some sort of backup is present." There is no soft delete and no backup
table in this schema — no is_deleted or deleted_at column on either model, no
archive table, no database trigger or rule. What actually happened is narrower
and duller: delegates/bulk_delete/ deleted the DELEGATE rows and left the
BookEvent invoice standing.

An invoice with no delegates is invisible in the Bookings table, because that
table lists delegate rows. It is NOT invisible anywhere else. It keeps its
unique invoice_number, so the webhook and every importer — all of which upsert
on invoice_number — matched it on the next payload and re-created its delegates,
which is exactly what a restored-from-backup delete looks like from the UI. It
also kept counting toward the dashboard aggregates that read BookEvent, and kept
being exported by the Data API's `bookings` resource.

    python manage.py test book_delegate.tests_hard_delete
"""
from django.contrib.auth import get_user_model
from django.db import connection
from rest_framework.test import APITestCase

from book_delegate.models import BookDelegate
from book_event.models import BookEvent

User = get_user_model()


class BulkDeleteHardDeleteTests(APITestCase):

    def setUp(self):
        # Username "HP" is the account crm_permissions.py lets past every module
        # gate (accounts/permissions.py dapi_USERNAME), which is what the person
        # running a mass delete in production is signed in as.
        self.admin = User.objects.create_user(
            username="HP", email="hp-del@iq-hub.com", password="x", role="admin")
        self.client.force_authenticate(self.admin)
        self.invoice = BookEvent.objects.create(
            invoice_number="INV-HARD-1", event_code="AFS - JS", total_amount=1000)
        self.delegates = [
            BookDelegate.objects.create(invoice=self.invoice, first_name="A",
                                        last_name=str(i), email=f"hard{i}@b.com")
            for i in range(2)
        ]

    def _raw_count(self, table, invoice_number):
        """Counted in SQL, so a manager that filtered rows out cannot hide them."""
        with connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE invoice_number = %s",
                        [invoice_number])
            return cur.fetchone()[0]

    def _bulk_delete(self, ids):
        return self.client.post("/api/delegates/bulk_delete/", {"ids": ids}, format="json")

    def test_the_rows_are_gone_from_the_table_not_flagged(self):
        resp = self._bulk_delete([d.id for d in self.delegates])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["deleted"], 2)
        self.assertEqual(self._raw_count("book_delegates", "INV-HARD-1"), 0)

    def test_the_emptied_invoice_is_deleted_too(self):
        self._bulk_delete([d.id for d in self.delegates])
        self.assertEqual(self._raw_count("book_events", "INV-HARD-1"), 0)

    def test_the_response_reports_the_invoices_it_removed(self):
        resp = self._bulk_delete([d.id for d in self.delegates])
        self.assertEqual(resp.data["invoices_deleted"], 1)

    def test_an_invoice_that_still_has_a_delegate_survives(self):
        resp = self._bulk_delete([self.delegates[0].id])
        self.assertEqual(resp.data["invoices_deleted"], 0)
        self.assertEqual(self._raw_count("book_events", "INV-HARD-1"), 1)
        self.assertEqual(self._raw_count("book_delegates", "INV-HARD-1"), 1)

    def test_an_untouched_empty_invoice_is_left_alone(self):
        # A website booking whose delegates have not arrived yet. This request
        # did not empty it, so it is none of this request's business.
        BookEvent.objects.create(invoice_number="INV-EMPTY", event_code="AFS - JS")
        self._bulk_delete([d.id for d in self.delegates])
        self.assertEqual(self._raw_count("book_events", "INV-EMPTY"), 1)

    def test_a_second_invoice_in_the_same_batch_is_emptied_too(self):
        other = BookEvent.objects.create(invoice_number="INV-HARD-2",
                                         event_code="AFS - JS")
        d = BookDelegate.objects.create(invoice=other, first_name="B",
                                        last_name="C", email="hard2@b.com")
        resp = self._bulk_delete([self.delegates[0].id, self.delegates[1].id, d.id])
        self.assertEqual(resp.data["invoices_deleted"], 2)
        self.assertEqual(self._raw_count("book_events", "INV-HARD-1"), 0)
        self.assertEqual(self._raw_count("book_events", "INV-HARD-2"), 0)

    def test_deleting_the_invoice_cascades_to_its_delegates(self):
        # The other delete path — the modal's "Delete booking". db_constraint=False
        # on the FK means the cascade is Django's, not the database's, so it is
        # worth pinning that it actually runs.
        resp = self.client.delete(f"/api/invoices/{self.invoice.pk}/")
        self.assertIn(resp.status_code, (200, 204))
        self.assertEqual(self._raw_count("book_events", "INV-HARD-1"), 0)
        self.assertEqual(self._raw_count("book_delegates", "INV-HARD-1"), 0)

    def test_a_deleted_booking_leaves_nothing_behind_to_re_match(self):
        """
        The re-creation mechanism, stated as a test: after a full delete the
        invoice_number is free, so an upsert on it INSERTS a new row rather than
        reviving the one that was deleted.
        """
        self._bulk_delete([d.id for d in self.delegates])
        self.assertFalse(BookEvent.objects.filter(invoice_number="INV-HARD-1").exists())
        revived, created = BookEvent.objects.get_or_create(
            invoice_number="INV-HARD-1", defaults={"event_code": "AFS - JS"})
        self.assertTrue(created, "an upsert matched a row that should not exist")
        self.assertIsNone(revived.total_amount, "the old invoice's data came back")
