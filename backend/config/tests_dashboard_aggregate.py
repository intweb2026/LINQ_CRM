"""
config/tests_dashboard_aggregate.py
────────────────────────────────────
Covers the two defects behind "the sales team and SpEx team data is not visible
on the dashboard", and the date-range filter added alongside them.

WHAT WAS WRONG, MEASURED ON THE 2026-06-11 SNAPSHOT

1.  ATTRIBUTION READ A COLUMN THAT IS EMPTY.
    Per-member booking counts came from `invoice.sales_executive`, which is NULL
    on all 2,230 invoices. Every member of every team therefore reported 0
    bookings and 0% conversion — a whole section of the dashboard reading as
    "no data" while 3,000 delegate rows sat in the table. Ownership does exist,
    in the event catalogue (193 of 217 events name a sales executive), so that is
    now the fallback. Sales Team went from 0 to 931 attributed bookings.

2.  THE PIPELINE SPLIT USED UNANCHORED SUBSTRING MATCHING.
    SpEx / speaker / delegate came from inline `booking_code__icontains="spp"`
    style clauses, the exact bug book_event/booking_code.py exists to end: "SPP"
    is three characters and matches inside "SUPPLEMENT". The dashboard now shares
    that module's anchored predicates, so the classification behind the numbers
    is the same one the Bookings screens use.

WHAT IS STILL ZERO, AND WHY THAT IS NOT A BUG
`Event.spex_team` is empty on all 217 events, so SpEx bookings cannot be split by
person at all. (`Event.speaker_sales_team` was the same and is now gone: the
Speaker Sales team is merged into SCA, and the speaker pipeline takes its owner
from `Event.sales_team` alongside Sales.) The view reports that
as `attribution_available: False` with the pipeline's real totals beside it,
rather than as a 0 indistinguishable from "sold nothing". Two tests below pin
that distinction; it is the difference between a missing number and a wrong one.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from config.views import PERIOD_DAYS, DashboardAggregateView, period_window
from events.models import Event
from teams.models import Team

User = get_user_model()

URL = "/api/stats/dashboard_aggregate/"


def get(user, period=None):
    """The view through DRF, so request.query_params exists."""
    url = URL if period is None else f"{URL}?period={period}"
    request = APIRequestFactory().get(url)
    force_authenticate(request, user=user)
    return DashboardAggregateView.as_view()(request)


class DashboardAggregateTestCase(TestCase):
    """
    Fixture shape mirrors the real data rather than a convenient abstraction:
    invoice.sales_executive is left NULL everywhere, because that is the state
    the production snapshot is in and the state the old code silently reported
    zero for. Ownership is expressed only through the event catalogue.
    """

    def setUp(self):
        # DashboardAggregateView now caches its response for 120s, keyed on
        # (period, resolved scope), and _owner_by_event() for 300s. LocMemCache
        # is per-PROCESS, not per-test, and every test in this class asks for the
        # same period as the same admin — so without this the first test's
        # payload is served to all the others, and each one asserts against
        # fixtures it cannot see. Django rolls back the DATABASE between tests;
        # it knows nothing about the cache.
        cache.clear()

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.admin = User.objects.create_user(
            username="dash.admin", password="x", role=User.Role.ADMIN,
            first_name="Dash", last_name="Admin", email="dash.admin@iq-hub.com",
        )
        cls.rep = User.objects.create_user(
            username="rep.one", password="x", role=User.Role.SALES,
            first_name="Rep", last_name="One", email="rep.one@iq-hub.com",
        )
        cls.sponsor_rep = User.objects.create_user(
            username="spex.one", password="x", role=User.Role.SPEX,
            first_name="Spex", last_name="One", email="spex.one@iq-hub.com",
        )

        # Team.save() slugs the name; User.save() derives a member's role from
        # the team name, so these two teams also fix their members' roles.
        cls.sales_team = Team.objects.create(name="Sales Team", color="#0ea5e9")
        cls.spex_team = Team.objects.create(name="SpEx Team", color="#06b6d4")
        cls.rep.team = cls.sales_team
        cls.rep.save()
        cls.sponsor_rep.team = cls.spex_team
        cls.sponsor_rep.save()

        # Event.save() writes sales_team from the FK, so the catalogue names the
        # rep both ways — the FK path is what the view prefers.
        # spex_team is left blank, exactly as all 217 rows of the real catalogue
        # have it.
        cls.event = Event.objects.create(
            event_code="TST", official_event_name="Test Event",
            event_date=cls.today + timedelta(days=30), sales_executive=cls.rep,
        )

    def make_booking(self, number, booking_code, when, status="Paid", company="Acme"):
        """One invoice and one delegate on it, dated `when` via request_date."""
        invoice = BookEvent.objects.create(
            invoice_number=number, event_code="TST", booking_code=booking_code,
            request_date=when, invoice_date=when, payment_status=status,
            company_name=company, total_amount=1000,
        )
        BookDelegate.objects.create(
            invoice=invoice, event_code="TST", booking_code=booking_code,
            first_name="Del", last_name=number, email=f"{number}@example.com",
        )
        return invoice

    # ── The pipeline split ───────────────────────────────────────────────────

    def test_pipeline_split_is_anchored_not_substring(self):
        """
        "SUPPLEMENT" contains "spp" and must NOT count as speaker sales. This is
        the failure book_event/booking_code.py was written for, and the dashboard
        used to reproduce it with its own inline icontains clauses.
        """
        self.make_booking("INV-SPEX", "SLV SpEx", self.today)
        self.make_booking("INV-SPKR", "Speaker", self.today)
        self.make_booking("INV-DEL", "Delegate", self.today)
        self.make_booking("INV-TRAP", "SUPPLEMENT", self.today)

        data = get(self.admin).data
        self.assertEqual(data["spex"]["total"], 1)
        self.assertEqual(data["speaker"]["total"], 1, "SUPPLEMENT leaked into speaker sales")
        self.assertEqual(data["sales"]["total"], 2, "SUPPLEMENT should fall through to delegate sales")

    def test_pipelines_sum_to_the_total_in_every_bucket(self):
        """
        The three lines are exclusive by construction. If they ever overlap, the
        dashboard's own cards disagree with each other and nothing says so.
        """
        self.make_booking("INV-A", "GLD SpEx", self.today)
        self.make_booking("INV-B", "Speaker / GLD SpEx", self.today)   # hybrid: SpEx wins
        self.make_booking("INV-C", "SPP", self.today, status="Pending")
        self.make_booking("INV-D", "Group Pass", self.today, status="Cancelled")

        data = get(self.admin).data
        for bucket in ("total", "paid", "pending", "free", "credit", "unpaid", "cancelled"):
            self.assertEqual(
                data["sales"][bucket] + data["spex"][bucket] + data["speaker"][bucket],
                data["all"][bucket],
                f"pipelines do not sum to all in bucket {bucket!r}",
            )
        self.assertEqual(data["spex"]["total"], 2, "hybrid code should count as SpEx, once")

    def test_companies_ignore_blanks_and_are_not_double_counted(self):
        """
        A blank company_name is a gap, not a company. And a company that both
        sponsors and sends delegates is ONE company in the `all` line.
        """
        self.make_booking("INV-S", "SLV SpEx", self.today, company="Globex")
        self.make_booking("INV-D", "Delegate", self.today, company="Globex")
        self.make_booking("INV-N", "Delegate", self.today, company="")

        data = get(self.admin).data
        self.assertEqual(data["spex"]["companies"], 1)
        self.assertEqual(data["sales"]["companies"], 1, "blank company_name counted as a company")
        self.assertEqual(data["all"]["companies"], 1, "Globex counted once per pipeline")
        self.assertEqual(data["all"]["invoices"], 3)

    # ── Attribution ──────────────────────────────────────────────────────────

    def test_bookings_attribute_through_the_event_when_the_invoice_fk_is_null(self):
        """
        THE REPORTED BUG. invoice.sales_executive is NULL on every row here, as
        it is on all 2,230 production invoices. The rep still has to see numbers.
        """
        self.make_booking("INV-1", "Delegate", self.today)
        self.make_booking("INV-2", "Delegate", self.today, status="Pending")
        self.assertFalse(BookEvent.objects.exclude(sales_executive=None).exists())

        data = get(self.admin).data
        sales = next(t for t in data["booking_team_productivity"]
                     if t["team_id"] == self.sales_team.id)
        self.assertEqual(sales["bookings"], 2)
        self.assertEqual(sales["paid"], 1)
        self.assertEqual(sales["conv"], 50)
        member = next(m for m in sales["members"] if m["user_id"] == self.rep.id)
        self.assertEqual(member["bookings"], 2)
        self.assertEqual(member["paid"], 1)
        self.assertTrue(sales["attribution_available"])

    def test_invoice_sales_executive_wins_over_the_event_fallback(self):
        """
        The fallback is a fallback. Where the invoice names an owner, that is the
        owner — otherwise re-homing a single booking would be silently ignored.
        """
        other = User.objects.create_user(
            username="rep.two", password="x", role=User.Role.SALES,
            first_name="Rep", last_name="Two", email="rep.two@iq-hub.com",
            team=self.sales_team,
        )
        invoice = self.make_booking("INV-1", "Delegate", self.today)
        invoice.sales_executive = other
        invoice.save()

        data = get(self.admin).data
        sales = next(t for t in data["booking_team_productivity"]
                     if t["team_id"] == self.sales_team.id)
        by_id = {m["user_id"]: m["bookings"] for m in sales["members"]}
        self.assertEqual(by_id[other.id], 1)
        self.assertEqual(by_id[self.rep.id], 0, "event fallback overrode an explicit invoice owner")

    def test_spex_reports_unavailable_attribution_rather_than_a_bare_zero(self):
        """
        Event.spex_team is empty on every event, so SpEx bookings cannot be
        attributed to a person. The response must distinguish that from "the SpEx
        team sold nothing" — the two are identical if only `bookings` is
        published, and confusing them is what made the SpEx card look broken.
        """
        self.make_booking("INV-S1", "SLV SpEx", self.today, company="Globex")
        self.make_booking("INV-S2", "PLT SpEx", self.today, company="Initech")

        data = get(self.admin).data
        spex = next(t for t in data["booking_team_productivity"]
                    if t["team_id"] == self.spex_team.id)
        self.assertEqual(spex["bookings"], 0)
        self.assertFalse(spex["attribution_available"])
        self.assertEqual(spex["pipeline_total"], 2, "the pipeline total must still be reported")
        self.assertEqual(spex["pipeline_companies"], 2)
        self.assertEqual(spex["pipeline_unattributed"], 2)
        self.assertEqual(spex["attribution_source"], "Event.spex_team")
        self.assertEqual(data["attribution"]["spex"]["events_mapped"], 0)

    def test_populating_spex_team_attributes_the_sponsor_rep(self):
        """The other half of the above: fill the column in and it works."""
        self.event.spex_team = "Spex One"
        self.event.save()
        self.make_booking("INV-S1", "SLV SpEx", self.today)

        data = get(self.admin).data
        spex = next(t for t in data["booking_team_productivity"]
                    if t["team_id"] == self.spex_team.id)
        self.assertTrue(spex["attribution_available"])
        self.assertEqual(spex["bookings"], 1)
        member = next(m for m in spex["members"] if m["user_id"] == self.sponsor_rep.id)
        self.assertEqual(member["bookings"], 1)

    def test_team_name_free_text_resolves_exact_only(self):
        """
        A name that matches nobody must resolve to nobody — not to whoever it is
        a substring of. accounts/user_resolution.py documents this at length; the
        dashboard has to honour it, and report the miss.
        """
        # queryset.update(), not save(): Event.save() clears sales_team whenever
        # sales_executive becomes None (events/models.py:111), so a name-only row
        # cannot be built through it. The production rows carrying a name and no
        # FK arrived through the CSV importer, which writes in bulk — this is the
        # same state, reached the same way.
        Event.objects.filter(pk=self.event.pk).update(
            sales_executive=None, sales_team="Rep",   # a prefix of "Rep One"
        )
        self.make_booking("INV-1", "Delegate", self.today)

        data = get(self.admin).data
        sales = next(t for t in data["booking_team_productivity"]
                     if t["team_id"] == self.sales_team.id)
        self.assertEqual(sales["bookings"], 0, "'Rep' substring-matched 'Rep One'")
        self.assertEqual(data["attribution"]["unattributed_delegates"], 1)
        unresolved = [v["value"] for v
                      in data["attribution"]["name_resolution"]["unresolved_values"]]
        self.assertIn("Rep", unresolved, "an unresolved name must be reported, not swallowed")

    def test_non_booking_teams_are_not_given_a_sales_pipeline(self):
        """
        team_type used to fall through to "sales" for any name without a keyword,
        so the Admin team appeared among the booking pipelines with 0 bookings.
        Dominant member role decides; the name is only consulted for empty teams.
        """
        admin_team = Team.objects.create(name="Admin")
        self.admin.team = admin_team
        self.admin.save()
        Team.objects.create(name="DMD Team")

        data = get(self.admin).data
        by_name = {t["team_name"]: t for t in data["booking_team_productivity"]}
        self.assertEqual(by_name["Admin"]["team_type"], "admin")
        self.assertEqual(by_name["Admin"]["pipeline"], "")
        self.assertEqual(by_name["DMD Team"]["team_type"], "data_mining",
                         "an empty DMD team must not read as a sales team")
        self.assertEqual(by_name["Sales Team"]["pipeline"], "sales")
        self.assertEqual(by_name["SpEx Team"]["pipeline"], "spex")

    # ── The date-range filter ────────────────────────────────────────────────

    def test_window_boundaries_are_inclusive_of_today_and_the_first_day(self):
        expected = {
            "all": (None, None),
            "last_7_days": (self.today - timedelta(days=6), self.today),
            "last_30_days": (self.today - timedelta(days=29), self.today),
            "last_12_months": (self.today - timedelta(days=364), self.today),
        }
        self.assertEqual(set(expected), set(PERIOD_DAYS))
        for key, window in expected.items():
            self.assertEqual(period_window(key, self.today), window, key)

    def test_every_period_filters_the_pipelines_and_the_months(self):
        self.make_booking("INV-TODAY", "Delegate", self.today)
        self.make_booking("INV-20D", "Delegate", self.today - timedelta(days=20))
        self.make_booking("INV-200D", "Delegate", self.today - timedelta(days=200))
        self.make_booking("INV-3Y", "Delegate", self.today - timedelta(days=1100))

        expected = {"all": 4, "last_7_days": 1, "last_30_days": 2, "last_12_months": 3}
        for period, count in expected.items():
            with self.subTest(period=period):
                data = get(self.admin, period).data
                self.assertEqual(data["all"]["total"], count)
                self.assertEqual(data["sales"]["total"], count)
                self.assertEqual(sum(m["total"] for m in data["months"]), count,
                                 "the monthly chart must agree with the cards")
                self.assertEqual(data["period"]["key"], period)

    def test_the_period_also_scopes_team_attribution(self):
        """
        A filter that moves the headline numbers but leaves team productivity at
        its all-time value is worse than no filter — the two are read side by
        side and would contradict each other.
        """
        self.make_booking("INV-NEW", "Delegate", self.today)
        self.make_booking("INV-OLD", "Delegate", self.today - timedelta(days=90))

        for period, count in (("all", 2), ("last_7_days", 1)):
            with self.subTest(period=period):
                data = get(self.admin, period).data
                sales = next(t for t in data["booking_team_productivity"]
                             if t["team_id"] == self.sales_team.id)
                self.assertEqual(sales["bookings"], count)
                self.assertEqual(sales["pipeline_total"], count)

    def test_the_resolved_window_is_echoed_back(self):
        data = get(self.admin, "last_7_days").data
        self.assertEqual(data["period"]["from"],
                         (self.today - timedelta(days=6)).isoformat())
        self.assertEqual(data["period"]["to"], self.today.isoformat())
        self.assertEqual(data["period"]["days"], 7)

        allp = get(self.admin, "all").data["period"]
        self.assertIsNone(allp["from"])
        self.assertIsNone(allp["to"])

    def test_an_unknown_period_is_rejected_not_ignored(self):
        response = get(self.admin, "last_month")
        self.assertEqual(response.status_code, 400)
        self.assertIn("last_month", response.data["detail"])
        # Every valid key is listed, so the caller can correct itself.
        for key in PERIOD_DAYS:
            self.assertIn(key, response.data["detail"])

    def test_outstanding_work_ignores_the_window(self):
        """
        The action queue is a worklist. Under "Last 7 days" the windowed line
        rightly shows one pending booking; `outstanding` must still show both,
        because a filter that reports "nothing unpaid" while a backlog exists is
        actively misleading.
        """
        self.make_booking("INV-OLD", "Delegate", self.today - timedelta(days=200),
                          status="Pending")
        self.make_booking("INV-NEW", "Delegate", self.today, status="Pending")

        window = get(self.admin, "last_7_days").data
        self.assertEqual(window["all"]["pending"], 1, "the analytic line should be scoped")
        self.assertEqual(window["outstanding"]["pending"], 2, "the worklist must not be")
        self.assertEqual(window["outstanding"]["total"], 2)
        self.assertEqual(get(self.admin, "all").data["outstanding"]["pending"], 2)

    def test_outstanding_is_still_rbac_scoped(self):
        """
        All-time is not the same as unscoped.

        This used to assert that the rep saw 0 until `assigned_events` was
        populated, which pinned the second half of defect 1 in this module's
        header. Attribution was moved onto the event catalogue and the SCOPE was
        left on the M2M, so the dashboard credited the rep with bookings it then
        refused to let them see. `assigned_events` is empty on all 45 accounts and
        `assigned_users` on all 217 events, so that read as an empty Bookings page
        for every non-admin. Ownership through the catalogue now scopes as well as
        attributes; see accounts/models.py assigned_event_codes.
        """
        self.make_booking("INV-1", "Delegate", self.today, status="Pending")

        # Owning nothing still shows nothing, which is what this test is for.
        stranger = User.objects.create_user(
            username="rep.two", password="x", role=User.Role.SALES,
            first_name="Rep", last_name="Two", email="rep.two@iq-hub.com",
            team=self.sales_team,
        )
        self.assertEqual(get(stranger).data["outstanding"]["pending"], 0)

        # The catalogue names the rep on TST, and that is the only way ownership
        # is expressed here, so the worklist is theirs with no M2M row at all.
        self.assertFalse(self.rep.assigned_events.exists())
        self.assertEqual(get(self.rep).data["outstanding"]["pending"], 1)

        # The M2M still grants it to somebody the catalogue does not name.
        stranger.assigned_events.add(self.event)
        self.assertEqual(get(stranger).data["outstanding"]["pending"], 1)

    def test_undated_bookings_are_reported_and_kept_out_of_windows(self):
        """
        A booking with neither request_date nor invoice_date cannot be placed in
        time. It belongs to "all" and to no window — and the count is published
        so the rows are not simply missing without explanation.
        """
        dated = self.make_booking("INV-D", "Delegate", self.today)
        self.assertIsNotNone(dated.request_date)
        undated = BookEvent.objects.create(
            invoice_number="INV-U", event_code="TST", booking_code="Delegate",
            payment_status="Paid", company_name="Acme", total_amount=500,
        )
        BookDelegate.objects.create(
            invoice=undated, event_code="TST", booking_code="Delegate",
            first_name="No", last_name="Date", email="nodate@example.com",
        )

        every = get(self.admin, "all").data
        self.assertEqual(every["all"]["total"], 2)
        self.assertEqual(every["period"]["undated_records"], 1)

        window = get(self.admin, "last_7_days").data
        self.assertEqual(window["all"]["total"], 1)
        self.assertEqual(window["period"]["undated_records"], 1)

    # ── RBAC ─────────────────────────────────────────────────────────────────

    def test_a_non_admin_sees_only_assigned_events(self):
        """
        _event_codes() scoping still applies with the new attribution and window
        code in the same query.
        """
        self.make_booking("INV-1", "Delegate", self.today)
        other_event = Event.objects.create(
            event_code="OTH", official_event_name="Other",
            event_date=self.today + timedelta(days=10),
        )
        BookEvent.objects.create(
            invoice_number="INV-2", event_code="OTH", booking_code="Delegate",
            request_date=self.today, payment_status="Paid", total_amount=100,
        )
        BookDelegate.objects.create(
            invoice=BookEvent.objects.get(invoice_number="INV-2"),
            event_code="OTH", booking_code="Delegate",
            first_name="Else", last_name="Where", email="elsewhere@example.com",
        )

        self.rep.assigned_events.add(self.event)
        scoped = get(self.rep).data
        self.assertEqual(scoped["all"]["total"], 1)

        self.rep.assigned_events.add(other_event)
        self.assertEqual(get(self.rep).data["all"]["total"], 2)
        self.assertEqual(get(self.admin).data["all"]["total"], 2)


class PeriodWindowTests(TestCase):
    """period_window() in isolation — no database, no view."""

    def test_all_has_no_bounds(self):
        self.assertEqual(period_window("all", date(2026, 8, 14)), (None, None))

    def test_a_seven_day_window_spans_seven_days(self):
        start, end = period_window("last_7_days", date(2026, 8, 14))
        self.assertEqual(start, date(2026, 8, 8))
        self.assertEqual(end, date(2026, 8, 14))
        self.assertEqual((end - start).days + 1, 7)

    def test_windows_cross_month_and_year_ends(self):
        self.assertEqual(period_window("last_30_days", date(2027, 1, 5))[0],
                         date(2026, 12, 7))
        self.assertEqual(period_window("last_12_months", date(2026, 3, 1))[0],
                         date(2025, 3, 2))
