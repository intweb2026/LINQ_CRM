"""
accounts/tests_period_filter.py
────────────────────────────────
The shared `?period=` window, and the two type traps it exists to get right.

WHY THE TRAPS MATTER
Both are silent, and both produce a plausible-looking wrong number:

1.  A DateTimeField compared against a DATE is compared against MIDNIGHT. So
    `created_at__lte=today` excludes everything that happened today after 00:00 —
    on a "last 7 days" window over Ticket Central, that is most of what the user
    is looking for, and the table simply shows fewer rows with no error anywhere.
    day_bounds() returns a half-open datetime interval instead.

2.  COALESCE over a DateField and a DateTimeField raises "Expression contains
    mixed types" — loudly, but only when that code path runs. Paper Review dates
    by paper_submission_date (date) falling back to created_at (datetime), which
    is exactly that mix, so the trap sits on a real configuration rather than a
    hypothetical one.

The mixin is also asserted NOT to narrow detail routes: fetching one booking by
id must not 404 because the user left "Last 7 days" selected.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.period_filter import (
    PERIOD_DAYS, PeriodError, apply_period, day_bounds, period_window,
    resolve_period, undated_count,
)
from book_delegate.models import BookDelegate
from book_delegate.views import BookDelegateViewSet
from book_event.models import BookEvent
from ticket_central.models import Ticket
from ticket_central.views import TicketViewSet
from teams.models import Team

User = get_user_model()


class WindowArithmeticTests(TestCase):
    """No database. Just the dates."""

    def test_all_is_unbounded(self):
        self.assertEqual(period_window("all", date(2026, 8, 14)), (None, None))

    def test_windows_include_today_and_their_first_day(self):
        today = date(2026, 8, 14)
        for key, days in PERIOD_DAYS.items():
            if days is None:
                continue
            start, end = period_window(key, today)
            self.assertEqual(end, today, key)
            self.assertEqual((end - start).days + 1, days, key)

    def test_windows_cross_month_and_year_ends(self):
        self.assertEqual(period_window("last_30_days", date(2027, 1, 5))[0], date(2026, 12, 7))
        self.assertEqual(period_window("last_12_months", date(2026, 3, 1))[0], date(2025, 3, 2))

    def test_resolve_rejects_an_unknown_key(self):
        with self.assertRaises(PeriodError) as ctx:
            resolve_period("last_month")
        for key in PERIOD_DAYS:
            self.assertIn(key, str(ctx.exception))

    def test_blank_and_absent_both_mean_all(self):
        for raw in (None, "", "   "):
            self.assertEqual(resolve_period(raw)[0], "all")

    def test_day_bounds_covers_the_whole_of_the_last_day(self):
        """
        The half-open interval, which is the fix for trap 1. The end bound is
        midnight on the day AFTER p_to, so 23:59 on p_to is inside it.
        """
        start, end = day_bounds(date(2026, 8, 7), date(2026, 8, 13))
        self.assertEqual(start.date(), date(2026, 8, 7))
        self.assertEqual((start.hour, start.minute), (0, 0))
        self.assertEqual(end.date(), date(2026, 8, 14))
        self.assertEqual((end.hour, end.minute), (0, 0))


class DateTimeColumnTests(TestCase):
    """Trap 1, against the database, on the column that actually carries it."""

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()

    def make_ticket(self, purpose, created):
        ticket = Ticket.objects.create(purpose=purpose, event_code="TST")
        Ticket.objects.filter(pk=ticket.pk).update(created_at=created)
        return ticket

    def test_a_row_created_later_today_is_inside_the_window(self):
        """
        The regression. With `created_at__lte=<date>` this row is compared against
        midnight and excluded — a ticket raised this afternoon vanishing from
        "Last 7 days" while the tab count above it still counted it.
        """
        late = timezone.make_aware(
            timezone.datetime.combine(self.today, timezone.datetime.min.time())
        ) + timedelta(hours=23, minutes=59)
        self.make_ticket("late today", late)

        _, p_from, p_to = resolve_period("last_7_days")
        qs = apply_period(Ticket.objects.all(), ("created_at",), p_from, p_to)
        self.assertEqual(qs.count(), 1)

    def test_rows_outside_the_window_are_excluded(self):
        now = timezone.now()
        self.make_ticket("today", now)
        self.make_ticket("old", now - timedelta(days=40))

        _, p_from, p_to = resolve_period("last_7_days")
        self.assertEqual(
            apply_period(Ticket.objects.all(), ("created_at",), p_from, p_to).count(), 1)
        _, p_from, p_to = resolve_period("last_12_months")
        self.assertEqual(
            apply_period(Ticket.objects.all(), ("created_at",), p_from, p_to).count(), 2)


class MixedTypeCoalesceTests(TestCase):
    """
    Trap 2. A date column falling back to a datetime one, which is Paper Review's
    and Proposal Submission's real configuration.
    """

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()

    def test_a_date_column_coalesced_with_a_datetime_one_runs(self):
        from paper_review.models import PaperReview

        PaperReview.objects.create(
            event_code="TST", speaker_name="Dated", email="dated@example.com",
            paper_submission_date=self.today,
        )
        blank = PaperReview.objects.create(
            event_code="TST", speaker_name="Undated", email="undated@example.com",
        )
        PaperReview.objects.filter(pk=blank.pk).update(
            created_at=timezone.now() - timedelta(days=400))

        fields = ("paper_submission_date", "created_at")
        _, p_from, p_to = resolve_period("last_7_days")
        qs = apply_period(PaperReview.objects.all(), fields, p_from, p_to)
        self.assertEqual(qs.count(), 1, "only the row dated today is in the window")

        _, p_from, p_to = resolve_period("last_12_months")
        self.assertEqual(
            apply_period(PaperReview.objects.all(), fields, p_from, p_to).count(), 1,
            "the row falling back to a created_at 400 days ago stays outside",
        )

    def test_the_fallback_is_what_keeps_a_blank_column_datable(self):
        """
        Without the created_at fallback, a review with no submission date would sit
        outside EVERY window — the reason the second field is there at all.
        """
        from paper_review.models import PaperReview

        blank = PaperReview.objects.create(
            event_code="TST", speaker_name="Undated", email="u@example.com",
        )
        self.assertIsNone(blank.paper_submission_date)

        _, p_from, p_to = resolve_period("last_7_days")
        with_fallback = apply_period(
            PaperReview.objects.all(), ("paper_submission_date", "created_at"), p_from, p_to)
        without = apply_period(
            PaperReview.objects.all(), ("paper_submission_date",), p_from, p_to)
        self.assertEqual(with_fallback.count(), 1)
        self.assertEqual(without.count(), 0)


class UndatedTests(TestCase):
    def test_a_row_with_no_date_is_outside_every_window(self):
        invoice = BookEvent.objects.create(
            invoice_number="INV-U", event_code="TST", payment_status="Paid")
        BookDelegate.objects.create(
            invoice=invoice, event_code="TST", first_name="No", last_name="Date",
            email="nodate@example.com")

        fields = ("invoice__request_date", "invoice__invoice_date")
        self.assertEqual(undated_count(BookDelegate.objects.all(), fields), 1)
        _, p_from, p_to = resolve_period("last_12_months")
        self.assertEqual(
            apply_period(BookDelegate.objects.all(), fields, p_from, p_to).count(), 0)
        # "all" leaves the queryset alone, so it is still there.
        _, p_from, p_to = resolve_period("all")
        self.assertEqual(
            apply_period(BookDelegate.objects.all(), fields, p_from, p_to).count(), 1)


class MixinWiringTests(TestCase):
    """The mixin on a real viewset: list narrows, detail does not, bad key 400s."""

    @classmethod
    def setUpTestData(cls):

        role = Team.objects.create(
            name="period_admin", is_all_access=True)
        cls.admin = User.objects.create_user(
            username="period.admin", password="x", role=User.Role.ADMIN,
            email="period.admin@iq-hub.com")
        cls.admin.team = role
        cls.admin.save()

        cls.today = timezone.localdate()
        cls.recent = cls._booking("INV-NEW", cls.today)
        cls.old = cls._booking("INV-OLD", cls.today - timedelta(days=200))

    @classmethod
    def _booking(cls, number, when):
        invoice = BookEvent.objects.create(
            invoice_number=number, event_code="TST", request_date=when,
            invoice_date=when, payment_status="Paid")
        return BookDelegate.objects.create(
            invoice=invoice, event_code="TST", first_name="Del", last_name=number,
            email=f"{number}@example.com")

    def _list(self, query=""):
        request = APIRequestFactory().get(f"/api/delegates/{query}")
        force_authenticate(request, user=self.admin)
        return BookDelegateViewSet.as_view({"get": "list"})(request)

    def test_the_list_is_narrowed(self):
        self.assertEqual(self._list("?period=all").data["count"], 2)
        self.assertEqual(self._list("?period=last_7_days").data["count"], 1)
        self.assertEqual(self._list().data["count"], 2, "no param means all")

    def test_the_window_is_echoed_in_the_response_headers(self):
        response = self._list("?period=last_7_days")
        self.assertEqual(response["X-Period"], "last_7_days")
        self.assertEqual(response["X-Period-From"],
                         (self.today - timedelta(days=6)).isoformat())
        self.assertEqual(response["X-Period-To"], self.today.isoformat())

    def test_an_unknown_key_is_a_400(self):
        response = self._list("?period=last_month")
        self.assertEqual(response.status_code, 400)
        self.assertIn("last_month", str(response.data))

    def test_a_detail_route_is_never_narrowed(self):
        """
        The window belongs to a list. Applying it in get_queryset() instead would
        make this 404 — fetching a booking by id would fail because the booking is
        older than the range the user happened to leave selected.
        """
        request = APIRequestFactory().get(f"/api/delegates/{self.old.id}/?period=last_7_days")
        force_authenticate(request, user=self.admin)
        response = BookDelegateViewSet.as_view({"get": "retrieve"})(request, pk=self.old.id)
        self.assertEqual(response.status_code, 200)

    def test_ticket_stats_take_the_same_window_as_the_ticket_list(self):
        """
        The tab counts sit directly above the rows. If the two took different
        windows, "Completed (35,690)" would label a table showing eleven rows.
        """
        Ticket.objects.create(purpose="recent", event_code="TST", status="completed")
        stale = Ticket.objects.create(purpose="old", event_code="TST", status="completed")
        Ticket.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(days=90))

        def stats(period):
            request = APIRequestFactory().get(f"/api/tickets/stats/?period={period}")
            force_authenticate(request, user=self.admin)
            return TicketViewSet.as_view({"get": "stats"})(request).data

        def rows(period):
            request = APIRequestFactory().get(f"/api/tickets/?period={period}")
            force_authenticate(request, user=self.admin)
            return TicketViewSet.as_view({"get": "list"})(request).data["count"]

        for period, expected in (("all", 2), ("last_7_days", 1)):
            with self.subTest(period=period):
                self.assertEqual(stats(period)["total"], expected)
                self.assertEqual(rows(period), expected)
