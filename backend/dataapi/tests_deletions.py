"""
dataapi/tests_deletions.py
───────────────────────────
The deletions feed, from the delete that should leave a tombstone to the row
the consumer should stop showing.

THE REPORT THIS LOCKS DOWN
"Total data on the CRM is 15322 but the API key pulled 15353, and it is still
pulling the deleted entries." It was not. Every export model is hard deleted
(book_delegate/tests_hard_delete.py pins that), so a deleted row cannot come
back down the wire from any endpoint here. What it could not do was say that
the row had GONE: ?updated_since= returns rows that still exist, so a consumer
polling it holds its copy for ever and its count only ever climbs. The 31 extra
were the accumulated deletes of every poll since the sheet was first filled.

    python manage.py test dataapi.tests_deletions
"""
import datetime
from urllib.parse import quote

from django.test import TestCase
from django.utils import timezone

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from dataapi.models import DataApiKey, DeletedRecord

HEADER = "HTTP_X_DATA_API_KEY"


class DeletionTombstoneTests(TestCase):
    """The post_delete receiver, per delete path."""

    def setUp(self):
        self.invoice = BookEvent.objects.create(
            invoice_number="INV-DEL-1", event_code="DEL-26",
            company_name="ACME", total_amount=100,
            request_date=datetime.date(2026, 3, 4),
        )
        self.ada = BookDelegate.objects.create(
            invoice=self.invoice, event_code="DEL-26",
            first_name="Ada", last_name="Lovelace", email="ada-del@example.com",
        )

    def _tombstones(self, resource):
        return set(DeletedRecord.objects
                   .filter(resource=resource)
                   .values_list("record_id", flat=True))

    def test_a_single_delete_leaves_one_tombstone(self):
        pk = self.ada.pk
        self.ada.delete()
        self.assertEqual(self._tombstones("delegates"), {pk})

    def test_a_queryset_delete_leaves_one_tombstone_per_row(self):
        # The shape delegates/bulk_delete/ uses. A queryset delete instantiates
        # nothing itself, so this is only true because post_delete still fires
        # per row.
        bob = BookDelegate.objects.create(
            invoice=self.invoice, event_code="DEL-26",
            first_name="Bob", last_name="Bly", email="bob-del@example.com",
        )
        pks = {self.ada.pk, bob.pk}
        BookDelegate.objects.filter(pk__in=pks).delete()
        self.assertEqual(self._tombstones("delegates"), pks)

    def test_a_cascade_from_the_invoice_tombstones_both_resources(self):
        # The modal's "Delete booking". The delegate rows go with the invoice,
        # and the consumer has to be told about both.
        invoice_pk, delegate_pk = self.invoice.pk, self.ada.pk
        self.invoice.delete()
        self.assertEqual(self._tombstones("bookings"), {invoice_pk})
        self.assertEqual(self._tombstones("delegates"), {delegate_pk})

    def test_an_edit_leaves_no_tombstone(self):
        self.ada.first_name = "Augusta"
        self.ada.save()
        self.assertFalse(DeletedRecord.objects.exists())


class DeletionEndpointTests(TestCase):
    def setUp(self):
        self.key, self.raw = DataApiKey.create_key(name="Full", scopes=[])
        self.invoice = BookEvent.objects.create(
            invoice_number="INV-DEL-2", event_code="DEL-26", total_amount=100)
        self.ada = BookDelegate.objects.create(
            invoice=self.invoice, event_code="DEL-26",
            first_name="Ada", last_name="Lovelace", email="ada-ep@example.com",
        )

    def _get(self, query="", raw_key=None):
        kwargs = {HEADER: raw_key or self.raw}
        return self.client.get(f"/api/data/deletions/{query}", **kwargs)

    def test_a_deleted_delegate_comes_back_as_a_tombstone(self):
        pk = self.ada.pk
        self.ada.delete()
        body = self._get("?resource=delegates").json()
        self.assertEqual(body["resource"], "deletions")
        self.assertEqual([r["record_id"] for r in body["results"]], [pk])

    def test_resource_filters_out_the_other_resources(self):
        self.invoice.delete()  # tombstones one booking AND one delegate
        self.assertEqual(len(self._get("?resource=bookings").json()["results"]), 1)
        self.assertEqual(len(self._get("").json()["results"]), 2)

    def test_deleted_since_returns_only_what_went_after_the_watermark(self):
        self.ada.delete()
        watermark = timezone.now()
        later = BookDelegate.objects.create(
            invoice=self.invoice, event_code="DEL-26",
            first_name="Bob", last_name="Bly", email="bob-ep@example.com",
        )
        later_pk = later.pk
        later.delete()
        # quote(): the `+` in a `+00:00` offset is a space once a query string is
        # decoded, which is a 400 from _apply_param_filter, not a silent
        # empty page. A consumer building this URL has to encode it too.
        body = self._get(
            f"?resource=delegates&deleted_since={quote(watermark.isoformat())}").json()
        self.assertEqual([r["record_id"] for r in body["results"]], [later_pk])

    def test_no_key_returns_401(self):
        self.assertEqual(self.client.get("/api/data/deletions/").status_code, 401)

    def test_a_scoped_key_reaches_its_own_resource_and_no_other(self):
        _, raw = DataApiKey.create_key(name="Delegates only", scopes=["delegates"])
        self.assertEqual(self._get("?resource=delegates", raw).status_code, 200)
        self.assertEqual(self._get("?resource=bookings", raw).status_code, 403)

    def test_a_scoped_key_cannot_ask_for_every_resource_at_once(self):
        # No ?resource= means all tombstones, which is wider than the key holds.
        _, raw = DataApiKey.create_key(name="Delegates only", scopes=["delegates"])
        self.assertEqual(self._get("", raw).status_code, 403)

    def test_an_unparseable_watermark_is_a_400_not_a_500(self):
        # The unencoded-`+` case, and every other malformed timestamp with it.
        # _apply_param_filter is shared, so this covers ?updated_since= on all
        # four export resources as well.
        self.assertEqual(
            self._get("?resource=delegates&deleted_since=not-a-date").status_code, 400)

    def test_an_unknown_resource_is_a_400_not_an_empty_page(self):
        # An empty page here reads as "nothing was deleted", and a consumer
        # that believes it deletes nothing and never notices the typo.
        self.assertEqual(self._get("?resource=delegate").status_code, 400)
