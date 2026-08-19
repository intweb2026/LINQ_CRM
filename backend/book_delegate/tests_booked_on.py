"""
book_delegate/tests_booked_on.py
─────────────────────────────────
The denormalised Bookings sort key.

WHAT THIS PINS, AND WHY EACH ONE MATTERS
1.  The derivation is COALESCE(request_date, invoice_date), including the
    all-NULL case. A wrong derivation is not a slow page, it is rows in the
    wrong order with no error anywhere.
2.  Changing an invoice's dates propagates to that invoice's delegates and to
    NO OTHER invoice's. The propagation is the one sanctioned queryset
    .update() in the codebase and it is filtered on a varchar FK column, which
    is exactly the kind of filter that silently matches everything or nothing.
3.  Deriving it costs NO EXTRA QUERY on the path the app actually uses. The
    whole point of the denormalisation is to remove work; a save() that added a
    SELECT would hand it straight back.
4.  The default ordering really is booked_on with the pk tiebreak, read off the
    compiled query rather than off the class attribute — StableOrderingFilter
    rewrites ordering at filter time, so the attribute is not the answer.
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

    def test_saving_with_no_date_change_leaves_delegates_alone(self):
        """
        The guard, not just the update. Without _dates_changed every invoice
        save would rewrite every one of its delegate rows.
        """
        self.inv_a.company_name = "Something Else"
        with CaptureQueriesContext(connection) as ctx:
            self.inv_a.save()
        updates = [q["sql"] for q in ctx.captured_queries
                   if q["sql"].lstrip().upper().startswith("UPDATE")
                   and "book_delegates" in q["sql"]]
        self.assertEqual(updates, [], "delegates were rewritten on a non-date save")


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

    def test_default_ordering_is_booked_on_desc_with_pk_tiebreak(self):
        self.assertEqual(self.compiled_ordering(), ["-booked_on", "pk"])

    def test_request_date_ordering_still_reaches_the_database(self):
        """
        _sort_request_date is kept in ordering_fields on purpose: BookingsPage.jsx
        sends it as the Request Date column's serverOrdering, and DRF silently
        DROPS an unlisted term rather than erroring. If this regresses, that
        column stops sorting and nothing reports it.
        """
        self.assertEqual(
            self.compiled_ordering("?ordering=-_sort_request_date"),
            ["-_sort_request_date", "pk"],
        )

    def test_rows_come_back_newest_first(self):
        """End to end, so the ordering is proven against real rows."""
        BookEvent.objects.create(
            invoice_number="ORDBO-2", event_code="ORDBO - AA",
            request_date="2026-09-09",
        )
        newer = BookDelegate.objects.create(
            invoice=BookEvent.objects.get(invoice_number="ORDBO-2"),
            event_code="ORDBO - AA", first_name="Newest", email="new@example.com",
        )
        factory = APIRequestFactory()
        req = factory.get("/api/delegates/")
        force_authenticate(req, user=self.user)
        resp = BookDelegateViewSet.as_view({"get": "list"})(req)
        resp.render()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["results"][0]["id"], newer.id)
