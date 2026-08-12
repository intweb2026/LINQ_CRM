"""
accounts/tests_write_scoping.py
────────────────────────────────
Mutating actions must operate on the SCOPED queryset, not the default manager.

THE BUG THIS LOCKS DOWN
book_delegate.bulk_delete ran `BookDelegate.objects.filter(id__in=ids)`. Any
caller past the IsAdminRole gate could therefore delete ANY delegate row by id,
regardless of event assignment — a wider reach than the same role's READ access,
because reads go through get_queryset() -> rbac_filter_invoice().

THE USER THAT MAKES IT EXPLOITABLE
IsAdminRole admits three kinds of caller (accounts/permissions.py):
    username == "HP"  OR  user.is_admin  OR  custom_role.is_all_access
but `is_admin` is `role == "admin"` (accounts/models.py:140), and
rbac_filter() only short-circuits for `is_admin`. So a user with
    role="sales"  +  custom_role.is_all_access=True
passes the permission gate while STILL being scoped — the combination this suite
uses throughout. Such a user existed in the live database at the time of writing.

No data survives: TestCase rolls each test back, and Django runs against a
separate test database, so `linq_crm` is never touched.

    python manage.py test accounts.tests_write_scoping
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import CustomRole
from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from events.models import Event
from ticket_central.models import Ticket
from ticket_central.views import TicketViewSet

User = get_user_model()

DELEGATE_BULK_DELETE = BookDelegateViewSet.as_view({"post": "bulk_delete"})
TICKET_BULK_DELETE = TicketViewSet.as_view({"post": "bulk_delete"})

IN_SCOPE = "SCOPED - AA"
OUT_OF_SCOPE = "HIDDEN - ZZ"


class DelegateBulkDeleteScopingTests(TestCase):
    """bulk_delete must not reach outside the caller's assigned events."""

    @classmethod
    def setUpTestData(cls):
        # Passes IsAdminRole via is_all_access, but is NOT role=admin, so
        # rbac_filter still scopes it. This is the dangerous combination.
        cls.all_access_role = CustomRole.objects.create(
            name="ws_all_access", display_label="WS All Access", is_all_access=True,
        )
        cls.scoped = User.objects.create_user(
            username="ws_scoped", password="x", role="sales", email="ws1@iq-hub.com",
        )
        cls.scoped.custom_role = cls.all_access_role
        cls.scoped.save()

        # A real admin: unrestricted by design, used to prove the fix did not
        # break legitimate admin deletion.
        cls.admin = User.objects.create_user(
            username="ws_admin", password="x", role="admin", email="ws2@iq-hub.com",
        )
        cls.admin.custom_role = cls.all_access_role
        cls.admin.save()

        # Only the in-scope event is assigned to the scoped user.
        cls.event_in = Event.objects.create(
            event_code=IN_SCOPE, name="Scoped Event", event_date=date(2026, 6, 1))
        cls.event_out = Event.objects.create(
            event_code=OUT_OF_SCOPE, name="Hidden Event", event_date=date(2026, 6, 2))
        cls.scoped.assigned_events.add(cls.event_in)

    def setUp(self):
        self.factory = APIRequestFactory()
        self.inv_in = BookEvent.objects.create(invoice_number="WS-IN", event_code=IN_SCOPE)
        self.inv_out = BookEvent.objects.create(invoice_number="WS-OUT", event_code=OUT_OF_SCOPE)
        self.d_in = BookDelegate.objects.create(
            invoice=self.inv_in, event_code=IN_SCOPE,
            first_name="In", last_name="Scope", email="in@example.com",
        )
        self.d_out = BookDelegate.objects.create(
            invoice=self.inv_out, event_code=OUT_OF_SCOPE,
            first_name="Out", last_name="Scope", email="out@example.com",
        )

    def _delete(self, user, ids):
        req = self.factory.post("/", {"ids": ids}, format="json")
        force_authenticate(req, user=user)
        resp = DELEGATE_BULK_DELETE(req)
        resp.render()
        return resp

    def test_scoped_caller_cannot_delete_out_of_scope_row(self):
        """The regression. Rejected, and the row must still exist afterwards."""
        resp = self._delete(self.scoped, [self.d_out.id])

        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertTrue(
            BookDelegate.objects.filter(id=self.d_out.id).exists(),
            "out-of-scope delegate was DELETED by a scoped caller",
        )

    def test_scoped_caller_can_delete_in_scope_row(self):
        """The fix must not cost the caller their legitimate reach."""
        resp = self._delete(self.scoped, [self.d_in.id])

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["deleted"], 1)
        self.assertFalse(BookDelegate.objects.filter(id=self.d_in.id).exists())

    def test_mixed_batch_deletes_only_the_in_scope_rows(self):
        """
        A batch spanning both scopes must delete the permitted rows and leave the
        rest — and SAY it skipped some, so a partial delete cannot read as a
        complete one.
        """
        resp = self._delete(self.scoped, [self.d_in.id, self.d_out.id])

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["deleted"], 1)
        self.assertEqual(resp.data["requested"], 2)
        self.assertEqual(resp.data["out_of_scope"], 1)
        self.assertFalse(BookDelegate.objects.filter(id=self.d_in.id).exists())
        self.assertTrue(
            BookDelegate.objects.filter(id=self.d_out.id).exists(),
            "out-of-scope delegate was deleted as collateral in a mixed batch",
        )

    def test_real_admin_is_still_unrestricted(self):
        """role=admin means assigned_event_codes() is None — genuinely global."""
        resp = self._delete(self.admin, [self.d_in.id, self.d_out.id])

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["deleted"], 2)
        self.assertEqual(BookDelegate.objects.filter(
            id__in=[self.d_in.id, self.d_out.id]).count(), 0)

    def test_scoped_caller_with_no_assigned_events_deletes_nothing(self):
        """
        rbac_filter returns qs.none() for a non-admin with no assigned events, so
        the scoped queryset is empty and nothing is deletable.
        """
        stranger = User.objects.create_user(
            username="ws_stranger", password="x", role="sales", email="ws3@iq-hub.com",
        )
        stranger.custom_role = self.all_access_role
        stranger.save()

        resp = self._delete(stranger, [self.d_in.id, self.d_out.id])

        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(BookDelegate.objects.filter(
            id__in=[self.d_in.id, self.d_out.id]).count(), 2)


class TicketBulkDeleteScopingTests(TestCase):
    """
    Ticket Central is DELIBERATELY cross-team visible: TicketViewSet.get_queryset()
    documents that it does not scope, because MR and DMD both work the whole queue.

    So this asserts the intended behaviour rather than scoping that does not exist.
    Its value is that bulk_delete now resolves through get_queryset(): if scoping is
    ever added there, this test starts failing and forces a decision, instead of the
    delete quietly staying global.
    """

    @classmethod
    def setUpTestData(cls):
        cls.role = CustomRole.objects.create(
            name="ws_tickets", display_label="WS Tickets", is_all_access=True,
        )
        cls.user = User.objects.create_user(
            username="ws_tk", password="x", role="sales", email="ws4@iq-hub.com",
        )
        cls.user.custom_role = cls.role
        cls.user.save()

    def setUp(self):
        self.factory = APIRequestFactory()
        self.t1 = Ticket.objects.create(event_code=IN_SCOPE, purpose="A")
        self.t2 = Ticket.objects.create(event_code=OUT_OF_SCOPE, purpose="B")

    def test_ticket_delete_is_intentionally_cross_team(self):
        req = self.factory.post("/", {"ids": [self.t1.id, self.t2.id]}, format="json")
        force_authenticate(req, user=self.user)
        resp = TICKET_BULK_DELETE(req)
        resp.render()

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["deleted"], 2)
        self.assertEqual(resp.data["out_of_scope"], 0)
