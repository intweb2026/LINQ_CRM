"""
book_delegate/tests_booked_on.py
─────────────────────────────────
The denormalised Bookings sort key.

WHAT THIS PINS, AND WHY EACH ONE MATTERS
1.  The derivation is COALESCE(request_date, invoice_date), including the
    all-NULL case. A wrong derivation is not a slow page, it is rows in the
    wrong order with no error anywhere.
2.  Changing an invoice's dates propagates to that invoice's delegates and to
    NO OTHER invoice's. The propagation is a sanctioned queryset .update() and
    it is filtered on a varchar FK column, which is exactly the kind of filter
    that silently matches everything or nothing. It now shares that statement
    with the delegates' updated_at stamp (BookEvent.DELEGATE_EXPORT_FIELDS), so
    the two conditions are pinned INDEPENDENTLY below: a save that moves an
    exported column but no date must write updated_at and not booked_on, and a
    save that moves neither must write nothing at all.
3.  Deriving it costs NO EXTRA QUERY on the path the app actually uses. The
    whole point of the denormalisation is to remove work; a save() that added a
    SELECT would hand it straight back.
4.  The default ordering really is ["-updated_at", "-id"], read off the compiled
    query rather than off the class attribute — StableOrderingFilter rewrites
    ordering at filter time, so the attribute is not the answer. booked_on is NO
    LONGER the sort; it stayed as the period window's column, and points 1-3
    above still pin it because the window depends on them. created_at is no longer
    the sort either, and is pinned here only as a term the Added Time column can
    still send.
5.  An EDITED row rises to the top. That is the whole point of the -updated_at
    default and it is the one property -created_at could not give: it is asserted
    against a row created FIRST and edited LAST, because a row that is both newest
    and last-edited would pass under either ordering and pin nothing.
"""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from teams.models import Team

User = get_user_model()


def bound_list_view(user, query=""):
    """
    A BookDelegateViewSet wired up enough to call get_queryset()/filter_queryset().

    Same construction as accounts/management/commands/pagination_walk.py: the
    viewset is instantiated with action/request/format_kwarg and then handed a
    real DRF Request carrying the user, because rbac_filter_invoice() reads
    self.request.user and StableOrderingFilter reads the query params.
    """
    from rest_framework.request import Request
    view = BookDelegateViewSet(action="list", request=None, format_kwarg=None)
    req = APIRequestFactory().get("/api/delegates/" + query)
    force_authenticate(req, user=user)
    drf_req = Request(req)
    drf_req.user = user
    view.request = drf_req
    view.kwargs = {}
    return view


class BookedOnDerivationTests(TestCase):
    """The value itself, across all three date shapes."""

    def test_request_date_wins_when_present(self):
        inv = BookEvent.objects.create(
            invoice_number="BO-1", event_code="BO - AA",
            request_date="2026-03-01", invoice_date="2026-01-01",
        )
        d = BookDelegate.objects.create(
            invoice=inv, event_code="BO - AA",
            first_name="A", email="a@example.com",
        )
        self.assertEqual(str(d.booked_on), "2026-03-01")

    def test_invoice_date_used_when_request_date_is_null(self):
        inv = BookEvent.objects.create(
            invoice_number="BO-2", event_code="BO - AA",
            request_date=None, invoice_date="2026-02-02",
        )
        d = BookDelegate.objects.create(
            invoice=inv, event_code="BO - AA",
            first_name="B", email="b@example.com",
        )
        self.assertEqual(str(d.booked_on), "2026-02-02")

    def test_null_when_the_invoice_carries_neither_date(self):
        inv = BookEvent.objects.create(
            invoice_number="BO-3", event_code="BO - AA",
            request_date=None, invoice_date=None,
        )
        d = BookDelegate.objects.create(
            invoice=inv, event_code="BO - AA",
            first_name="C", email="c@example.com",
        )
        self.assertIsNone(d.booked_on)

    def test_the_stored_column_matches_not_just_the_instance(self):
        """
        Re-read from the database. An in-memory attribute set on the instance
        but excluded from the INSERT would pass every assertion above and store
        nothing — editable=False makes that a live risk.
        """
        inv = BookEvent.objects.create(
            invoice_number="BO-4", event_code="BO - AA",
            request_date="2026-04-04",
        )
        d = BookDelegate.objects.create(
            invoice=inv, event_code="BO - AA",
            first_name="D", email="d@example.com",
        )
        stored = BookDelegate.objects.values_list("booked_on", flat=True).get(pk=d.pk)
        self.assertEqual(str(stored), "2026-04-04")


class BookedOnPropagationTests(TestCase):
    """BookEvent.save() -> every delegate on THAT invoice, and no other."""

    @classmethod
    def setUpTestData(cls):
        cls.inv_a = BookEvent.objects.create(
            invoice_number="PROP-A", event_code="PROP - AA",
            request_date="2026-01-01",
        )
        cls.inv_b = BookEvent.objects.create(
            invoice_number="PROP-B", event_code="PROP - AA",
            request_date="2026-01-01",
        )
        for i in range(3):
            BookDelegate.objects.create(
                invoice=cls.inv_a, event_code="PROP - AA",
                first_name=f"A{i}", email=f"a{i}@example.com",
            )
        for i in range(2):
            BookDelegate.objects.create(
                invoice=cls.inv_b, event_code="PROP - AA",
                first_name=f"B{i}", email=f"b{i}@example.com",
            )

    def booked(self, invoice):
        return sorted(
            str(v) for v in BookDelegate.objects
            .filter(invoice_id=invoice.invoice_number)
            .values_list("booked_on", flat=True)
        )

    def test_changing_request_date_updates_every_delegate_on_that_invoice(self):
        self.assertEqual(self.booked(self.inv_a), ["2026-01-01"] * 3)

        self.inv_a.request_date = "2026-06-15"
        self.inv_a.save()

        self.assertEqual(self.booked(self.inv_a), ["2026-06-15"] * 3)

    def test_no_delegate_on_another_invoice_moves(self):
        self.inv_a.request_date = "2026-06-15"
        self.inv_a.save()

        self.assertEqual(
            self.booked(self.inv_b), ["2026-01-01"] * 2,
            "the propagation filter matched delegates on a different invoice",
        )

    def test_clearing_request_date_falls_back_to_invoice_date(self):
        self.inv_a.invoice_date = "2026-02-02"
        self.inv_a.request_date = None
        self.inv_a.save()

        self.assertEqual(self.booked(self.inv_a), ["2026-02-02"] * 3)

    def _delegate_updates(self, mutate):
        """The UPDATE statements against book_delegates that `mutate` provokes."""
        mutate()
        with CaptureQueriesContext(connection) as ctx:
            self.inv_a.save()
        return [q["sql"] for q in ctx.captured_queries
                if q["sql"].lstrip().upper().startswith("UPDATE")
                and "book_delegates" in q["sql"]]

    def test_saving_with_no_date_change_does_not_rewrite_booked_on(self):
        """
        The guard, not just the update. Without _dates_changed every invoice
        save would rewrite booked_on on every one of its delegate rows.

        NARROWED FROM "no UPDATE AT ALL" TO "no booked_on". This block is no
        longer the booked_on cascade alone: BookEvent.save() now also stamps its
        delegates' updated_at when a column in DELEGATE_EXPORT_FIELDS moves, and
        company_name — the invoice's billing company, exported on the delegate
        row as account_company — is one of them. So this save legitimately does
        write book_delegates now, and what has to stay absent is booked_on. The
        no-write-at-all case is pinned by the test below, on a column no
        delegate is exported with.
        """
        updates = self._delegate_updates(
            lambda: setattr(self.inv_a, "company_name", "Something Else"))
        self.assertFalse(
            [sql for sql in updates if "booked_on" in sql],
            "booked_on was rewritten on a non-date save",
        )

    def test_saving_an_invoice_only_column_leaves_delegates_untouched(self):
        """
        Both guards at once, on a column that reaches neither cascade.
        total_amount is invoice-only: it is not a date, so booked_on does not
        move, and no delegate is exported with it, so no watermark moves either.
        A save like this must issue ZERO statements against book_delegates —
        otherwise every unrelated invoice write would push the whole invoice
        through the Data API's ?updated_since= delta feed.
        """
        updates = self._delegate_updates(
            lambda: setattr(self.inv_a, "total_amount", 4321))
        self.assertEqual(updates, [],
                         "delegates were rewritten on an invoice-only save")


class BookedOnQueryCostTests(TestCase):
    """The denormalisation must not cost a query on the path the app uses."""

    @classmethod
    def setUpTestData(cls):
        cls.team = Team.objects.create(name="bo_admin", is_all_access=True)
        cls.user = User.objects.create_user(
            username="bo_user", password="x", role="admin", email="bo@iq-hub.com",
        )
        cls.user.team = cls.team
        cls.user.save()
        inv = BookEvent.objects.create(
            invoice_number="COST-1", event_code="COST - AA",
            request_date="2026-05-05",
        )
        BookDelegate.objects.create(
            invoice=inv, event_code="COST - AA",
            first_name="Q", email="q@example.com",
        )

    def test_saving_a_delegate_from_the_viewset_queryset_adds_no_query(self):
        """
        get_queryset() select_relateds the invoice, so _derive_booked_on() must
        read it out of _state.fields_cache and issue nothing. Asserted by
        counting SELECTs against book_events during the save.
        """
        delegate = (bound_list_view(self.user).get_queryset()
                    .get(email="q@example.com"))

        with CaptureQueriesContext(connection) as ctx:
            delegate.save()

        invoice_selects = [
            q["sql"] for q in ctx.captured_queries
            if q["sql"].lstrip().upper().startswith("SELECT")
            and "book_events" in q["sql"]
        ]
        self.assertEqual(
            invoice_selects, [],
            "_derive_booked_on() fell back to a query despite a cached invoice",
        )

    def test_the_fallback_still_derives_correctly_without_select_related(self):
        """
        The other half: an instance whose invoice is NOT cached must still get
        the right value, via the two-column fallback.
        """
        delegate = BookDelegate.objects.get(email="q@example.com")
        self.assertNotIn("invoice", delegate._state.fields_cache)
        delegate.booked_on = None
        delegate.save()
        delegate.refresh_from_db()
        self.assertEqual(str(delegate.booked_on), "2026-05-05")


class BookedOnDefaultOrderingTests(TestCase):
    """
    The compiled ORDER BY, not the class attribute.

    StableOrderingFilter appends the pk at filter time, so
    BookDelegateViewSet.ordering alone does not tell you what the database is
    asked for. This reads view.filter_queryset(view.get_queryset()).query.order_by
    the way accounts' pagination_walk does.
    """

    @classmethod
    def setUpTestData(cls):
        cls.team = Team.objects.create(name="ord_bo_admin", is_all_access=True)
        cls.user = User.objects.create_user(
            username="ord_bo", password="x", role="admin", email="ordbo@iq-hub.com",
        )
        cls.user.team = cls.team
        cls.user.save()
        inv = BookEvent.objects.create(
            invoice_number="ORDBO-1", event_code="ORDBO - AA",
            request_date="2026-01-01",
        )
        for i in range(5):
            BookDelegate.objects.create(
                invoice=inv, event_code="ORDBO - AA",
                first_name=f"N{i}", email=f"n{i}@example.com",
            )

    def compiled_ordering(self, query=""):
        view = bound_list_view(self.user, query)
        qs = view.filter_queryset(view.get_queryset())
        return list(qs.query.order_by)

    def test_default_ordering_is_updated_at_desc_newest_modified_first(self):
        """
        The Bookings table's default is NEWEST MODIFIED FIRST.

        Not newest business date, which is what booked_on gave, and no longer
        newest ADDED either. -created_at pinned a row to its entry position for
        good: a correction made this morning to a row entered in July stayed in
        July, so the person who made it could not see their own work.

        -id is asserted, not pk: StableOrderingFilter appends `pk` ASCENDING,
        which resolves ties oldest-first inside a tied microsecond and would not
        match book_delegates_updated_id_idx. Spelling -id in the viewset's default
        makes the filter pass the ordering through untouched, and this pins that.
        """
        self.assertEqual(self.compiled_ordering(), ["-updated_at", "-id"])

    def test_updated_at_ordering_still_reaches_the_database(self):
        """
        BookingsPage.jsx sends `updated_at` as the Modified Time column's
        serverOrdering, and DRF silently DROPS a term that is not in
        ordering_fields — which is exactly why that header did nothing before
        this change. Explicit and default compile differently on purpose: an
        explicit term takes StableOrderingFilter's ascending pk tiebreak, which is
        deterministic and is all a user-picked sort needs.
        """
        self.assertEqual(
            self.compiled_ordering("?ordering=-updated_at"),
            ["-updated_at", "pk"],
        )

    def test_booked_on_ordering_still_reaches_the_database(self):
        """
        booked_on is off the default but stays in ordering_fields, and DRF
        silently DROPS an unlisted term rather than erroring. It is also still
        period_date_fields, so it has to keep working.
        """
        self.assertEqual(
            self.compiled_ordering("?ordering=-booked_on"),
            ["-booked_on", "pk"],
        )

    def test_created_at_ordering_still_reaches_the_database(self):
        """
        BookingsPage.jsx sends `created_at` as the Added Time column's
        serverOrdering. Explicit and default are deliberately NOT the same
        compiled order: an explicit term goes through StableOrderingFilter's pk
        tiebreak, which is deterministic and is all that column needs.
        """
        self.assertEqual(
            self.compiled_ordering("?ordering=-created_at"),
            ["-created_at", "pk"],
        )

    def test_request_date_ordering_still_reaches_the_database(self):
        """
        _sort_request_date is kept in ordering_fields on purpose: BookingsPage.jsx
        sends it as the Request Date column's serverOrdering, and DRF silently
        DROPS an unlisted term rather than erroring. If this regresses, that
        column stops sorting and nothing reports it.

        It compiles to an EXPRESSION rather than the plain '-_sort_request_date'
        string because the column is nullable and the view lists it in
        nulls_last_ordering_fields: Postgres would otherwise open "newest first"
        with every undated row. See accounts/ordering.py.
        """
        term, tiebreak = self.compiled_ordering("?ordering=-_sort_request_date")
        self.assertEqual(tiebreak, "pk")
        self.assertEqual(term.expression.name, "_sort_request_date")
        self.assertTrue(term.descending)
        self.assertTrue(term.nulls_last)

    def test_rows_come_back_newest_added_first(self):
        """
        End to end, so the ordering is proven against real rows.

        THE INVOICE DATE IS DELIBERATELY THE OLDEST IN THE SET. This is the
        original reported bug in miniature: a delegate entered LAST against an
        invoice raised FIRST. Under the old -booked_on default it sorted to the
        bottom, and it must not go back there.

        STILL VALID UNDER -updated_at, and not by luck: auto_now stamps updated_at
        on INSERT as well as on save, so a row nobody has edited since carries its
        creation instant and a never-edited set sorts identically either way. What
        this no longer pins on its own is the edit case, which is why the test
        below exists.
        """
        BookEvent.objects.create(
            invoice_number="ORDBO-2", event_code="ORDBO - AA",
            request_date="2020-01-01",
        )
        newest = BookDelegate.objects.create(
            invoice=BookEvent.objects.get(invoice_number="ORDBO-2"),
            event_code="ORDBO - AA", first_name="Newest", email="new@example.com",
        )
        self.assertEqual(str(newest.booked_on), "2020-01-01",
                         "the fixture no longer sets up the old-invoice case")
        factory = APIRequestFactory()
        req = factory.get("/api/delegates/")
        force_authenticate(req, user=self.user)
        resp = BookDelegateViewSet.as_view({"get": "list"})(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["results"][0]["id"], newest.id)

    def test_editing_an_old_row_moves_it_to_the_top(self):
        """
        THE PROPERTY THE CHANGE WAS ASKED FOR. A row created FIRST and edited LAST
        must be row one.

        Created first is the load-bearing half. Every other ordering this table
        has had — -booked_on, -created_at — puts this row at or near the BOTTOM,
        so a test that edited the newest row instead would pass under all three
        and prove nothing about the new default.

        The edit goes through instance.save(), which is the path
        BookDelegateListSerializer.update() takes and the path
        accounts/bulk_update.py takes, so auto_now is what does the stamping here
        rather than the test writing updated_at by hand. A test that assigned the
        timestamp would still pass if auto_now stopped firing.
        """
        oldest = BookDelegate.objects.order_by("created_at", "id").first()
        newest = BookDelegate.objects.order_by("-created_at", "-id").first()
        self.assertNotEqual(oldest.id, newest.id, "fixture has only one row")

        # Sanity: before the edit the table leads with the newest row, so the
        # assertion after it is measuring the edit and not the fixture order.
        self.assertEqual(self.list_ids()[0], newest.id)

        oldest.position = "Edited"
        oldest.save()

        self.assertGreater(oldest.updated_at, newest.updated_at,
                           "auto_now did not stamp updated_at on save()")
        self.assertEqual(self.list_ids()[0], oldest.id)

    def test_clearing_overrides_stamps_updated_at(self):
        """
        book_delegate/services.py clear_delegate_overrides() is a queryset
        .update(), which does NOT fire auto_now — the ORM never instantiates the
        rows, so no pre_save() runs. It sets updated_at explicitly for that
        reason, and this pins it: clearing a delegate's payment overrides is a
        real edit that visibly changes five cells, and without the explicit stamp
        it was the one edit that left the row where it was while every lesser edit
        floated to the top.
        """
        from book_delegate.services import DelegatePaymentOverrideResolver

        target = BookDelegate.objects.order_by("created_at", "id").first()
        target.delegate_payment_type = "Card"
        target.save()
        before = BookDelegate.objects.get(pk=target.pk).updated_at

        resolver = DelegatePaymentOverrideResolver(target.invoice)
        resolver.clear_delegate_overrides([target.id], fields=["payment_type"])

        after = BookDelegate.objects.get(pk=target.pk).updated_at
        self.assertGreater(after, before,
                           "clear_delegate_overrides() left updated_at untouched")
        self.assertEqual(self.list_ids()[0], target.id)

    def list_ids(self):
        """The ids the list endpoint returns, in order, under the default sort."""
        factory = APIRequestFactory()
        req = factory.get("/api/delegates/")
        force_authenticate(req, user=self.user)
        resp = BookDelegateViewSet.as_view({"get": "list"})(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        return [row["id"] for row in resp.data["results"]]
