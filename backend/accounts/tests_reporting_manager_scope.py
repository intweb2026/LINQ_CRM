"""
accounts/tests_reporting_manager_scope.py
──────────────────────────────────────────
A lead sees the Bookings and Events of the people who report to them.

THE REQUIREMENT
Data sharing scopes a sales person to the events assigned to them, through
`RBACMixin.rbac_filter` and the pair of ownership routes in
`User.assigned_event_codes`. Fred and Terry are both team leads, and each must
see the rows of the people mapped under THEM, which is `User.mapped_lead`, the
reporting manager.

THE REPORTING MANAGER IS THE WHOLE RULE
Not team membership and not the `is_team_lead` flag. Fred and Terry sit in one
team here on purpose, so the fixture would collapse into a single shared set
under a team-wide rule; `test_two_leads_in_one_team_see_different_sets` is what
holds them apart. `test_an_unmapped_member_belongs_to_nobody` is the other half
of the same point: a blank reporting manager shares with no one, so sharing is
opt in one filled-in field at a time.

WHAT MUST NOT MOVE
Everybody nobody reports to. `test_a_plain_member_is_unchanged`,
`test_a_plain_member_cannot_see_their_lead` and
`test_a_lead_with_nobody_mapped_sees_only_themselves` are the regression half;
the widening is additive and reaches an account only once somebody names it.

    python manage.py test accounts.tests_reporting_manager_scope
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.permissions import RBACMixin
from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event
from teams.models import Team

User = get_user_model()


class _Scope(RBACMixin):
    """rbac_filter reads self.request.user and nothing else."""

    def __init__(self, user):
        self.request = type("R", (), {"user": user})()

    def invoices(self):
        return self.rbac_filter(BookEvent.objects.all())

    def delegates(self):
        return self.rbac_filter_invoice(BookDelegate.objects.all())


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


class ReportingManagerScopeTests(TestCase):
    """
    ONE team holding both leads, so nothing here can pass by accident on team
    membership.

        Terry  (lead)  <- ann, gone (inactive)
        Fred   (lead)  <- bob
        loose          <- nobody, and reports to nobody
        outside        <- second team, reports to nobody

    Ownership is set through `Event.sales_executive` only, with no
    `assigned_events` row anywhere. That is the state of every row in the live
    snapshot, so a fixture leaning on the M2M would be exercising a path the real
    data does not use.
    """

    @classmethod
    def setUpTestData(cls):
        cls.sales = Team.objects.create(name="RMS Sales")
        cls.other_team = Team.objects.create(name="RMS Other")

        def _user(name, team, lead=False, manager=None, status="active"):
            return User.objects.create_user(
                username=name, password="x", role="sales",
                email=f"{name}@iq-hub.com", team=team,
                is_team_lead=lead, mapped_lead=manager, status=status,
            )

        cls.terry = _user("rms_terry", cls.sales, lead=True)
        cls.fred  = _user("rms_fred", cls.sales, lead=True)

        cls.ann   = _user("rms_ann", cls.sales, manager=cls.terry)
        cls.gone  = _user("rms_gone", cls.sales, manager=cls.terry,
                          status="inactive")
        cls.bob   = _user("rms_bob", cls.sales, manager=cls.fred)
        # Same team as everybody above, reporting manager left blank.
        cls.loose = _user("rms_loose", cls.sales)

        cls.outside = _user("rms_outside", cls.other_team)
        cls.admin   = User.objects.create_user(
            username="rms_admin", password="x", role="admin",
            email="rms_admin@iq-hub.com")

        # One event and one booking per person, so every row is traceable to the
        # single account that owns it.
        cls.rows = {}
        for label, person in (
            ("TERRY", cls.terry), ("FRED", cls.fred), ("ANN", cls.ann),
            ("GONE", cls.gone), ("BOB", cls.bob), ("LOOSE", cls.loose),
            ("OUT", cls.outside),
        ):
            code = f"RMS{label} - AA"
            Event.objects.create(
                event_code=code, name=f"Event {label}",
                event_date=date(2026, 9, 1), sales_executive=person)
            cls.rows[label] = _booking(f"INV-{label}", code)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _invoice_numbers(self, user):
        return sorted(i.invoice_number for i in _Scope(user).invoices())

    def _delegate_ids(self, user):
        return sorted(d.id for d in _Scope(user).delegates())

    def _event_codes(self, user):
        """The Events grid query, lifted from events/views.py get_queryset."""
        from django.db.models import Q
        scope_ids = user.data_scope_user_ids() or [user.pk]
        return sorted(
            Event.objects
            .filter(Q(assigned_users__in=scope_ids) | Q(sales_executive__in=scope_ids))
            .distinct()
            .values_list("event_code", flat=True)
        )

    # ── the requirement ───────────────────────────────────────────────────────

    def test_a_lead_sees_the_people_mapped_under_them(self):
        """
        THE REQUIREMENT. Terry owns one event and sees Ann's row on top of their
        own, because Ann's reporting manager is Terry.
        """
        self.assertEqual(self.terry.assigned_event_codes(), ["RMSTERRY - AA"])
        self.assertEqual(
            self._invoice_numbers(self.terry), ["INV-ANN", "INV-TERRY"])

    def test_two_leads_in_one_team_see_different_sets(self):
        """
        The point of the rule. Both leads sit in one team, so a team-wide scope
        would hand them one shared set; the reporting manager splits them, and
        neither reaches the other's report.
        """
        self.assertEqual(
            self._invoice_numbers(self.fred), ["INV-BOB", "INV-FRED"])
        self.assertNotIn("INV-BOB", self._invoice_numbers(self.terry))
        self.assertNotIn("INV-ANN", self._invoice_numbers(self.fred))

    def test_an_unmapped_member_belongs_to_nobody(self):
        """
        A blank reporting manager shares with nobody, however many leads sit in
        the same team. Sharing is opt in, one filled-in field at a time.
        """
        self.assertIsNone(self.loose.mapped_lead)
        for lead in (self.terry, self.fred):
            self.assertNotIn("INV-LOOSE", self._invoice_numbers(lead))
            self.assertNotIn("RMSLOOSE - AA", self._event_codes(lead))
        self.assertEqual(self._invoice_numbers(self.loose), ["INV-LOOSE"])

    def test_the_lead_sees_the_same_events(self):
        """
        The Events grid and the Bookings grid have to widen together. They are
        two separate queries over the same ownership pair, and a lead offered a
        booking on an event their Events grid hides is the mismatch
        `assigned_event_codes` was written to close in the first place.
        """
        self.assertEqual(
            self._event_codes(self.terry), ["RMSANN - AA", "RMSTERRY - AA"])

    def test_the_delegate_half_widens_too(self):
        """
        BookDelegate reaches its executive through `invoice__sales_executive`, a
        different code path from the invoice list. The two halves of one module
        disagreeing is a bug book_delegate/tests_scope.py already had to fix.
        """
        self.assertEqual(
            self._delegate_ids(self.terry),
            sorted(self.rows[k].id for k in ("TERRY", "ANN")),
        )

    def test_a_second_report_is_added_not_replaced(self):
        """Mapping is many to one, so a lead accumulates their reports."""
        self.loose.mapped_lead = self.terry
        self.loose.save(update_fields=["mapped_lead"])
        self.assertEqual(
            self._invoice_numbers(self.terry),
            ["INV-ANN", "INV-LOOSE", "INV-TERRY"],
        )

    # ── what must not move ────────────────────────────────────────────────────

    def test_a_plain_member_is_unchanged(self):
        """The widening reaches an account only once somebody names it."""
        self.assertEqual(self._invoice_numbers(self.ann), ["INV-ANN"])
        self.assertEqual(self._event_codes(self.ann), ["RMSANN - AA"])
        self.assertEqual(self.ann.data_scope_user_ids(), [self.ann.pk])

    def test_a_plain_member_cannot_see_their_lead(self):
        """Sharing runs down the reporting line, not up it."""
        self.assertNotIn("INV-TERRY", self._invoice_numbers(self.ann))

    def test_a_lead_with_nobody_mapped_sees_only_themselves(self):
        """
        `is_team_lead` grants nothing on its own. Only the field filled in on
        somebody else's row does, which is what "where the reporting manager is
        filled in" means.
        """
        alone = User.objects.create_user(
            username="rms_alone", password="x", role="sales",
            email="rms_alone@iq-hub.com", team=self.sales, is_team_lead=True)
        self.assertEqual(alone.data_scope_user_ids(), [alone.pk])
        self.assertEqual(self._invoice_numbers(alone), [])

    def test_a_lead_does_not_reach_another_team(self):
        self.assertNotIn("INV-OUT", self._invoice_numbers(self.terry))
        self.assertNotIn("RMSOUT - AA", self._event_codes(self.terry))

    def test_an_inactive_report_is_excluded(self):
        """
        An inactive account cannot sign in, so its rows would be visible to the
        lead and to nobody else. That is a quieter form of the orphaning this
        scope exists to prevent, so the report list is filtered to active.
        """
        self.assertEqual(self.gone.mapped_lead_id, self.terry.pk)
        self.assertNotIn("INV-GONE", self._invoice_numbers(self.terry))

    def test_clearing_the_manager_withdraws_the_row(self):
        """
        The scope is computed per request from the field, with nothing cached, so
        unmapping somebody takes their rows back immediately.
        """
        self.ann.mapped_lead = None
        self.ann.save(update_fields=["mapped_lead"])
        self.assertEqual(self._invoice_numbers(self.terry), ["INV-TERRY"])

    def test_admin_is_still_unrestricted(self):
        self.assertIsNone(self.admin.data_scope_user_ids())
        self.assertIsNone(self.admin.visible_event_codes())
        self.assertEqual(_Scope(self.admin).invoices().count(), 7)

    # ── writes ────────────────────────────────────────────────────────────────

    def test_a_report_row_is_writable_by_the_lead(self):
        """
        Full access, same as their own, was the choice made here. One scope
        serves reads and writes, so a row the lead can list is a row they can
        update or delete; `get_object`, `bulk_update` and `destroy` all run
        through this same `rbac_filter`.
        """
        scope = _Scope(self.terry).invoices()
        self.assertEqual(scope.filter(invoice_number="INV-ANN").count(), 1)
