"""
book_delegate/tests_modified_time_default_sort.py
──────────────────────────────────────────────────
The Bookings table leads with the row someone touched LAST. Edit anything, and
it is at the top on the next load.

WHY THIS FILE EXISTS SEPARATELY FROM tests_ordering.py
That file proves the ORDER BY is stable and index-shaped. This one proves the
promise the order is there to keep, which is a different thing and fails for
different reasons. The clause `ordering = ["-updated_at", "-id"]` can be
perfectly correct while an edit still fails to surface, because the write path
that made the edit never moved `updated_at`. A queryset `.update()` does not
fire `auto_now` — the ORM never instantiates the row, so no field's `pre_save()`
runs — and the Bookings modal, the invoice panel and the mass-update engine all
write that way in places. Each of those paths gets a test here, and each is
written as "the row is now first", not as "the column changed", because first is
what was asked for.

The negative cases matter as much as the positive ones. A table sorted by
Modified Time is only readable if a save that changed nothing leaves the order
alone, and if editing one person does not haul everybody on their invoice up
with them — the modal PATCHes the whole delegate list on every save, so that is
a live hazard rather than a theoretical one.

    python manage.py test book_delegate.tests_modified_time_default_sort
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from book_event.views import BookEventViewSet
from events.models import Event
from teams.models import Team

User = get_user_model()

CODE = "SORT - AA"
LIST = BookDelegateViewSet.as_view({"get": "list"})


class ModifiedTimeSortTests(TestCase):
    """Three invoices, one delegate each, entered oldest to newest."""

    @classmethod
    def setUpTestData(cls):
        cls.team = Team.objects.create(name="sort_all", is_all_access=True)
        cls.user = User.objects.create_user(
            username="sort_admin", password="x", role="admin", email="sa@iq-hub.com",
        )
        cls.user.team = cls.team
        cls.user.save()

    def setUp(self):
        self.factory = APIRequestFactory()
        Event.objects.create(event_code=CODE, official_event_name="Sort Event",
                             event_date="2026-06-01")
        self.people = {}
        for n, first in enumerate(["Ada", "Grace", "Alan"], start=1):
            invoice = BookEvent.objects.create(
                invoice_number=f"SORT-{n}", event_code=CODE, edition=2026,
                payment_status="Pending", currency="USD",
            )
            self.people[first] = BookDelegate.objects.create(
                invoice=invoice, event_code=CODE, edition=2026,
                first_name=first, last_name="Person",
                email=f"{first.lower()}@sort.test",
            )

    # ── helpers ───────────────────────────────────────────────────────────────

    def order(self):
        """The first names the Bookings table shows, top row first."""
        req = self.factory.get("/api/delegates/", {"page_size": 50})
        force_authenticate(req, user=self.user)
        resp = LIST(req)
        resp.render()
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        return [r["first_name"] for r in rows]

    def top(self):
        return self.order()[0]

    def patch_invoice(self, invoice, body):
        req = self.factory.patch(f"/api/invoices/{invoice.pk}/", body, format="json")
        force_authenticate(req, user=self.user)
        resp = BookEventViewSet.as_view({"patch": "partial_update"})(req, pk=invoice.pk)
        resp.render()
        return resp

    def bulk(self, body):
        req = self.factory.post("/api/delegates/bulk_update/", body, format="json")
        force_authenticate(req, user=self.user)
        resp = BookDelegateViewSet.as_view({"post": "bulk_update"})(req)
        resp.render()
        return resp

    def row(self, delegate, **overrides):
        row = {
            "id": delegate.id,
            "first_name": delegate.first_name,
            "last_name": delegate.last_name,
            "email": delegate.email,
        }
        row.update(overrides)
        return row

    # ── The default itself ────────────────────────────────────────────────────

    def test_the_default_is_modified_time_newest_first(self):
        self.assertEqual(BookDelegateViewSet.ordering, ["-updated_at", "-id"])
        # Entered Ada, Grace, Alan; untouched since, so the newest entry leads.
        self.assertEqual(self.order(), ["Alan", "Grace", "Ada"])

    def test_the_index_this_default_needs_is_really_there(self):
        """
        Declared is not the same as present in this project. `sync_indexes`
        exists because 36 declared indexes were missing from a database that
        reported every migration applied, so this reads pg_indexes rather than
        the model. On 130,000 delegates the difference is one index scan against
        sorting the whole table to return 50 rows.
        """
        from django.db import connection
        if connection.vendor != "postgresql":
            self.skipTest("pg_indexes is Postgres-specific")
        with connection.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'book_delegates' AND indexname = %s",
                ["book_delegates_updated_id_idx"],
            )
            found = cur.fetchone()
        self.assertIsNotNone(found, "book_delegates_updated_id_idx is missing")
        self.assertIn("updated_at DESC", found[0])
        self.assertIn("id DESC", found[0])

    # ── One test per write path that must surface ─────────────────────────────

    def test_a_delegate_edit_in_the_modal_rises(self):
        self.patch_invoice(
            self.people["Ada"].invoice,
            {"delegates": [self.row(self.people["Ada"], position="Owner")]},
        )
        self.assertEqual(self.top(), "Ada")

    def test_a_delegate_edit_sent_without_an_id_rises(self):
        """
        The modal sends no id for a row it thinks is new.

        Note what actually happens to such a payload, because it is not what the
        code around it reads as: any stored delegate whose id is absent from the
        payload is DELETED first, so a no-id row for somebody already stored is
        a delete followed by an insert, not an update. The row rises either way,
        which is what this test pins; it keeps its data and loses its id.
        """
        ada = self.people["Ada"]
        self.patch_invoice(ada.invoice, {"delegates": [{
            "first_name": ada.first_name, "last_name": ada.last_name,
            "email": ada.email, "position": "Owner",
        }]})
        stored = BookDelegate.objects.get(invoice=ada.invoice)
        self.assertEqual((stored.first_name, stored.position), ("Ada", "Owner"))
        self.assertEqual(self.top(), "Ada")

    def test_an_invoice_level_edit_rises(self):
        """
        Payment status lives on the invoice, and the delegates are the rows the
        table shows. BookEvent.save() stamps them when a column in
        DELEGATE_EXPORT_FIELDS moves.
        """
        self.patch_invoice(self.people["Ada"].invoice, {"payment_status": "Paid"})
        self.assertEqual(self.top(), "Ada")

    def test_a_bulk_edit_rises(self):
        ids, field, value = [self.people["Ada"].id], "attendance", "Confirmed"
        plan = self.bulk({"ids": ids, "field": field, "value": value, "commit": False})
        self.assertEqual(plan.status_code, 200, plan.data)
        resp = self.bulk({"ids": ids, "field": field, "value": value,
                          "commit": True, "plan_hash": plan.data["plan_hash"]})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.top(), "Ada")

    def test_an_invoice_rename_keeps_its_delegates_visible(self):
        self.patch_invoice(self.people["Ada"].invoice, {"invoice_number": "SORT-1-R"})
        self.assertEqual(self.top(), "Ada")

    # ── And the quiet cases ───────────────────────────────────────────────────

    def test_a_save_that_changes_nothing_does_not_reshuffle(self):
        before = self.order()
        self.patch_invoice(self.people["Ada"].invoice,
                           {"delegates": [self.row(self.people["Ada"])]})
        self.assertEqual(self.order(), before)

    def test_editing_one_delegate_does_not_haul_up_the_others(self):
        """
        The modal re-sends every delegate on the invoice, so without a
        did-anything-move test each save would stamp them all and a two-person
        booking would arrive at the top as a block.
        """
        invoice = self.people["Ada"].invoice
        bob = BookDelegate.objects.create(
            invoice=invoice, event_code=CODE, edition=2026,
            first_name="Bob", last_name="Bystander", email="bob@sort.test",
        )
        bob.refresh_from_db()
        untouched = bob.updated_at

        self.patch_invoice(invoice, {"delegates": [
            self.row(self.people["Ada"], position="Owner"),
            self.row(bob),
        ]})

        self.assertEqual(self.top(), "Ada")
        # Bob is second here only because he was the last row created. What must
        # hold is that the save did not TOUCH him; asserting his position would
        # pass for the wrong reason.
        bob.refresh_from_db()
        self.assertEqual(bob.updated_at, untouched)
