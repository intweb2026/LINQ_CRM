"""
pre_event_docs/tests_pre_event_docs.py
───────────────────────────────────────
Seven cases, covering the logic that will actually break. Not one per function.

The networking optimiser has its own file, tests_networking.py, because it is
pure Python with no database and belongs in a SimpleTestCase.
"""
import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event

from . import services
from .models import BadgeIssue
from .views import PreEventDocsViewSet

CODE = "PED"

# Sentinel, at MODULE level rather than on the base class. A default argument is
# evaluated in its own class's namespace, so a subclass method could not see it
# where it was, and threading None instead silently substituted a real date for
# the one test that checks a missing one.
_DEFAULT = object()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.inv = BookEvent.objects.create(
            invoice_number="PED-001", event_code=CODE, payment_status="Paid",
        )
        cls.user = get_user_model().objects.create(username="HP")
        # The change deadline is the EVENT start minus the window, so the tests
        # need a catalogue row with a date on it. Placed in the future so the
        # deadline arithmetic has both sides to check.
        # official_event_name, NOT name. Event.save() overwrites `name` with
        # official_event_name, or with the event_code when that is blank, so a
        # name assigned here would be silently replaced by "PED".
        # timezone.localdate(), NOT date.today(). change_deadline is compared
        # against timezone.localdate(), and the two disagree by a day whenever
        # the system clock and Django's TIME_ZONE sit either side of midnight.
        # This test asserted 46 days and got 47 the moment the date rolled over
        # mid-session, which is a real defect in the test rather than in the
        # code: a fixture and the thing it is measured against have to read the
        # same clock.
        cls.event = Event.objects.create(
            event_code=CODE, official_event_name="Pre-Event Docs Test",
            location="Munich, Germany",
            event_date=timezone.localdate() + timedelta(days=60),
        )

    def add(self, first, last="Jones", company="Acme Ltd", code="Delegate",
            email=None, invoice=None, **kw):
        return BookDelegate.objects.create(
            invoice=invoice or self.inv,
            event_code=CODE,
            first_name=first,
            last_name=last,
            company_name_raw=company,
            booking_code=code,
            email=email or f"{first}.{last}@example.com".lower(),
            **kw,
        )

    def rows(self):
        """The BADGE population, event only. What the badge tabs see."""
        return list(services.event_queryset(CODE))

    def desk(self):
        """The CHECK-IN SHEET population, the subset that report filters down to."""
        return [r["delegate_id"] for r in services.check_in(self.rows())]

    def _changes(self, event_date=_DEFAULT):
        """
        (additional, cancellations), the two lists badge_changes returns.

        The sentinel matters: None is a MEANINGFUL event_date here, it is what
        an event with no catalogue row looks like, so `or self.event.event_date`
        would substitute a date for the one case that tests its absence.
        """
        if event_date is _DEFAULT:
            event_date = self.event.event_date
        return services.badge_changes(self.rows(), CODE, event_date=event_date)

    def additional(self, event_date=_DEFAULT):
        """ADDITIONAL NAME BADGES only, keyed by delegate for easy assertion."""
        rows, _ = self._changes(event_date)
        return {r["delegate_id"]: r for r in rows}

    def cancellations(self, event_date=_DEFAULT):
        """CANCELLATIONS only, keyed by badge id."""
        _, rows = self._changes(event_date)
        return {r["badge_id"]: r for r in rows}


class TwoPopulations(Base):
    """
    THE WORKBOOK FILTERS ON ONE TAB ONLY, and this class is what pins it.

    Registered_Delegates and Report_NameBadges filter on the event and nothing
    else; only Report_CheckIn applies the payment whitelist and the exclusions.
    For DLG - VV that is 75 badge rows against 45 at the desk. An earlier
    version applied the desk filters to everything and printed 45 badges where
    the sheet prints 75.
    """

    def test_the_badge_population_is_the_event_and_nothing_else(self):
        keep = self.add("Real", code="Delegate")
        excluded = [self.add("Line", last=code.replace(" ", ""), code=code)
                    for code in services.EXCLUDED_BOOKING_CODES]
        internal = self.add("Ourselves", company="iQ-Hub")
        unpaid_inv = BookEvent.objects.create(
            invoice_number="PED-002", event_code=CODE, payment_status="Refunded")
        unpaid = self.add("Never", invoice=unpaid_inv)

        badged = {d.id for d in self.rows()}
        for d in [keep, internal, unpaid] + excluded:
            self.assertIn(d.id, badged,
                          "the badge list filters on the event only")
        self.assertEqual(len(badged), len(excluded) + 3)

    def test_only_the_check_in_sheet_filters(self):
        keep = self.add("Real", code="Delegate")
        for code in services.EXCLUDED_BOOKING_CODES:
            self.add("Line", last=code.replace(" ", ""), code=code)
        self.add("Ourselves", company="iQ-Hub")
        self.add("Staffer", email="staffer@iq-hub.com")
        unpaid_inv = BookEvent.objects.create(
            invoice_number="PED-002", event_code=CODE, payment_status="Refunded")
        self.add("Never", invoice=unpaid_inv)

        self.assertEqual(self.desk(), [keep.id],
                         "the desk sheet is the one that applies the filters")

    def test_media_does_not_exclude_a_company_called_mediacorp(self):
        """The over-match booking_code.py exists to prevent, asserted here too."""
        keep = self.add("Sunil", company="Mediacorp Media Group", code="Delegate")
        self.assertIn(keep.id, self.desk())

    def test_a_company_merely_containing_our_name_is_not_internal(self):
        """
        Why the company test is whole-cell and not a substring. The workbook
        compares the whole cell, and a substring test would drop a real delegate.
        """
        keep = self.add("Partner", company="iQ-Hub Ventures Partners LLP")
        self.assertIn(keep.id, self.desk())

    def test_there_is_no_attendance_filter_anywhere(self):
        """
        The workbook has none. A Cancelled PAYMENT STATUS is what removes a
        cancellation; the IN? column is the tick, not a filter.
        """
        cancelled_tick = self.add(
            "Ticked", attendance=BookDelegate.Attendance.CANCELLED)
        self.assertIn(cancelled_tick.id, {d.id for d in self.rows()})
        self.assertIn(cancelled_tick.id, self.desk())

    def test_delegate_override_beats_its_invoice_in_both_directions(self):
        """The whole reason book_delegate/effective.py is shared, not copied."""
        unpaid_inv = BookEvent.objects.create(
            invoice_number="PED-003", event_code=CODE, payment_status="Refunded")

        # Attending invoice, overridden to Cancelled: off the desk sheet.
        blocked = self.add("Overridden", invoice=self.inv,
                           delegate_payment_status="Cancelled")
        # Non-attending invoice, overridden to Paid: on it.
        rescued = self.add("Rescued", invoice=unpaid_inv,
                           delegate_payment_status="Paid")
        self.assertEqual(self.desk(), [rescued.id])
        self.assertNotIn(blocked.id, self.desk())


class TbaAndRoles(Base):
    def test_tba_is_dropped_from_the_name_badges(self):
        """
        DROPPED, not blanked. The workbook blanks the name and keeps the row;
        this drops it, on instruction. A blank badge is stock rather than a
        badge, so one per unnamed booking is a wasted badge and a nameless card
        on the table.
        """
        real = self.add("Priya", last="Nair")
        self.add("TBA", last="", company="Northwind Ltd")

        badges = services.name_badges(self.rows())
        self.assertEqual([b["delegate_id"] for b in badges], [real.id])

    def test_a_placeholder_becomes_a_badge_once_it_gets_a_name(self):
        """
        Why dropping it loses nothing. The row reappears through Additional Name
        Badges as soon as the booking is given a real name, which is exactly
        when a badge can usefully be printed for it.
        """
        placeholder = self.add("TBA", last="", company="Northwind Ltd")
        BadgeIssue.objects.create(
            delegate=self.add("Anchor", company="Acme Ltd"), event_code=CODE,
            name="Anchor Jones", company="Acme Ltd", issued_by=self.user)
        self.assertEqual(services.name_badges(self.rows())[0]["name"], "Anchor Jones")
        self.assertNotIn(placeholder.id, self.additional())

        placeholder.first_name, placeholder.last_name = "Priya", "Nair"
        placeholder.save()
        self.assertIn(placeholder.id, [b["delegate_id"]
                                       for b in services.name_badges(self.rows())])
        self.assertEqual(self.additional()[placeholder.id]["remark"], "New Badge")

    def test_a_real_name_containing_the_letters_tba_is_not_a_placeholder(self):
        """
        The whole-word rule. Two workbook tabs use a bare substring test, and a
        third a three character prefix, and all three drop these two people.
        """
        keep = self.add("Tbarak", last="Otbani", company="Mitbar Holdings")
        badges = services.name_badges(self.rows())
        self.assertEqual([b["delegate_id"] for b in badges], [keep.id],
                         "Tbarak is a person, not a placeholder")

    def test_a_placeholder_company_counts_as_tba_too(self):
        """Report_Additional tests both columns, so both are tested here."""
        self.add("Real", last="Person", company="TBA")
        self.assertEqual(services.name_badges(self.rows()), [],
                         "an unnamed COMPANY is a placeholder too")
        self.assertEqual(self.additional(), {})

    def test_the_role_column_matches_the_workbook_exactly(self):
        """
        Report_CheckIn tests SpEx FIRST and prints the WHOLE booking code when it
        matches, so a hybrid tells the desk both facts in one column.

        An earlier version of role_of inverted the precedence to make Speaker
        win, on the reasoning that the desk most needs to spot who is on stage.
        That threw away the sponsorship tier to answer a question the full code
        answers anyway. This pins the sheet behaviour so it does not come back.
        """
        self.assertEqual(services.role_of("Speaker / GLD SpEx"), "Speaker / GLD SpEx")
        self.assertEqual(services.role_of("GLD SpEx"), "GLD SpEx")
        self.assertEqual(services.role_of("Speaker"), "Speaker")
        self.assertEqual(services.role_of("SPP"), "Speaker")
        self.assertEqual(services.role_of("Delegate"), "")
        self.assertEqual(services.role_of(""), "")

    def test_pending_is_told_to_collect_payment_on_site(self):
        pending_inv = BookEvent.objects.create(
            invoice_number="PED-004", event_code=CODE, payment_status="Pending")
        self.add("Owes", invoice=pending_inv)
        self.add("Settled")
        notes = {r["name"]: r["payment_status_label"]
                 for r in services.check_in(self.rows())}
        # Hyphenated, as the workbook prints it.
        self.assertEqual(notes["Owes Jones"], "Payment to collect on-site")
        self.assertEqual(notes["Owes Jones"], services.COLLECT_ON_SITE_NOTE)
        self.assertEqual(notes["Settled Jones"], "")


class ChangeDetection(Base):
    def issue(self, delegate, name=None, company=None, days_ago=0):
        return BadgeIssue.objects.create(
            delegate=delegate,
            event_code=CODE,
            name=name if name is not None else delegate.full_name,
            company=company if company is not None else delegate.company_display,
            issued_at=timezone.now() - timedelta(days=days_ago),
            issued_by=self.user,
        )

    def to_print(self, event_date=_DEFAULT):
        """Older alias. It MUST pass the sentinel through, not None."""
        return self.additional(event_date)

    def test_the_lifecycle_a_first_time_event_actually_goes_through(self):
        """
        THE ORDER OF EVENTS, pinned end to end, because getting it wrong is the
        one thing that makes this report useless.

        1. Open a fresh event. Name Badges has everybody. Additional Name Badges
           is EMPTY, because nothing has been frozen so there is no "since".
        2. Freeze. Additional is still empty; the badges match the bookings.
        3. Change a booking. NOW Additional shows exactly that one change.

        An earlier version had no step-1 guard and listed every person on the
        check-in sheet as a New Badge before anything had been printed.
        """
        a = self.add("Ann", last="Gordon", company="Acme Ltd")
        b = self.add("Ben", last="Hall", company="Borax Ltd")

        # 1. Nothing frozen.
        self.assertEqual(len(services.name_badges(self.rows())), 2)
        self.assertEqual(self.additional(), {},
                         "a first-time event has no changes to report")

        # 2. Freeze both, exactly as the page does.
        run = uuid.uuid4()
        for d in (a, b):
            BadgeIssue.objects.create(
                delegate=d, event_code=CODE, name=d.full_name,
                company=d.company_display, run_id=run, issued_by=self.user)
        self.assertEqual(self.additional(), {},
                         "just frozen, so nothing has changed yet")

        # 3. One booking changes.
        b.company_name_raw = "Borax Holdings Ltd"
        b.save()
        rows = self.additional()
        self.assertEqual(list(rows), [b.id], "only the changed booking appears")
        self.assertEqual(rows[b.id]["remark"], "Company Change")
        self.assertEqual(rows[b.id]["was_company"], "Borax Ltd")

    def test_a_booking_added_after_the_freeze_is_a_new_badge(self):
        """The other half of step 3, and why New Badge is meaningful at all."""
        early = self.add("Early", company="Acme Ltd")
        BadgeIssue.objects.create(
            delegate=early, event_code=CODE, name=early.full_name,
            company="Acme Ltd", issued_by=self.user)

        late = self.add("Late", company="Zenith Ltd")
        rows = self.additional()
        self.assertEqual(list(rows), [late.id])
        self.assertEqual(rows[late.id]["remark"], "New Badge")

    def test_every_change_outcome(self):
        unchanged = self.add("Same", company="Acme Ltd")
        renamed = self.add("Renamed", company="Acme Ltd")
        moved = self.add("Moved", company="Acme Ltd")
        both = self.add("Both", company="Acme Ltd")
        fresh = self.add("Fresh", company="Acme Ltd")

        self.issue(unchanged)
        self.issue(renamed, name="Old Name")
        self.issue(moved, company="Old Company Ltd")
        self.issue(both, name="Old Name", company="Old Company Ltd")
        # `fresh` gets no badge at all.

        rows = self.to_print()
        self.assertNotIn(unchanged.id, rows, "an unchanged badge is not a change")
        self.assertEqual(rows[renamed.id]["remark"], "Name Change")
        self.assertEqual(rows[moved.id]["remark"], "Company Change")
        self.assertEqual(rows[both.id]["remark"], "Name & Company Change")
        self.assertEqual(rows[fresh.id]["remark"], "New Badge")
        self.assertEqual(rows[moved.id]["was_company"], "Old Company Ltd")

    def test_spelling_alone_is_not_a_change(self):
        d = self.add("Ann", last="Gordon", company="Acme  Ltd")
        self.issue(d, name="ann  gordon", company="Acme Ltd")
        self.assertNotIn(d.id, self.to_print())

    def test_the_deadline_is_the_event_start_minus_fourteen_calendar_days(self):
        """
        NOT a countdown from the badge run, which is what this was first built
        as. The deadline belongs to the EVENT, so it is the same for every row
        and is unaffected by how long ago a badge was issued.
        """
        early = self.add("Early")
        late = self.add("Late")
        self.issue(early, name="Old A", days_ago=1)
        self.issue(late, name="Old B", days_ago=300)

        rows = self.to_print()
        expected = self.event.event_date - timedelta(days=14)
        for row in rows.values():
            self.assertEqual(row["deadline"], expected)
        # 60 days out less the 14 day window, so 46 days of runway, and the
        # badge issued 300 days ago is in exactly the same position as the one
        # issued yesterday.
        self.assertEqual(rows[early.id]["days_left"], 46)
        self.assertEqual(rows[late.id]["days_left"], 46)
        self.assertTrue(rows[early.id]["in_window"])
        self.assertTrue(rows[late.id]["in_window"])

    def test_weekends_count_toward_the_deadline(self):
        """
        Calendar days, deliberately. A Monday event minus 14 days is a Monday,
        and the printer deadline does not care which day of the week it lands on.
        """
        monday = date(2026, 6, 1)
        self.assertEqual(monday.weekday(), 0)
        self.assertEqual(services.change_deadline(monday), date(2026, 5, 18))
        self.assertEqual(services.change_deadline(monday).weekday(), 0)

    def test_a_deadline_already_passed_is_marked_and_still_listed(self):
        stale = self.add("Stale")
        self.issue(stale, name="Old Name")
        past = date.today() - timedelta(days=5)

        rows = self.to_print(event_date=past)
        self.assertFalse(rows[stale.id]["in_window"])
        self.assertLess(rows[stale.id]["days_left"], 0)
        self.assertIn(stale.id, rows, "a change past the deadline must still be listed")

    def test_an_event_with_no_catalogue_date_reports_the_window_as_unknown(self):
        nameless = self.add("Nodate")
        self.issue(nameless, name="Old Name")
        rows = self.additional(event_date=None)
        self.assertIsNone(rows[nameless.id]["deadline"])
        self.assertIsNone(rows[nameless.id]["in_window"],
                          "unknown must not read as inside the window")

    def test_tba_is_never_chased_as_a_change(self):
        self.add("TBA", last="")
        self.assertEqual(self.to_print(), {})

    def test_take_out_survives_the_delegate_being_deleted(self):
        leaving = self.add("Leaving", company="Gone Ltd")
        self.issue(leaving)
        deleted = self.add("Deleted", company="Vanished Ltd")
        self.issue(deleted)

        # One drops off the DESK population, which is what the take out list
        # compares against; the other's row is removed outright.
        leaving.delegate_payment_status = "Cancelled"
        leaving.save()
        deleted.delete()

        removals = {r["name"]: r for r in self.cancellations().values()}
        self.assertIn("Deleted Jones", removals)
        self.assertEqual(removals["Deleted Jones"]["company"], "Vanished Ltd",
                         "a deleted delegate must still name whose badge to pull")
        # And no cancellation ever leaks onto the additional list.
        self.assertNotIn("Remove", [r["remark"] for r in self.additional().values()])

    def test_only_the_latest_badge_counts(self):
        d = self.add("Twice", company="Acme Ltd")
        self.issue(d, name="Very Old", days_ago=30)
        self.issue(d, days_ago=1)                    # current, matches
        self.assertNotIn(d.id, self.to_print())


class Endpoint(Base):
    def call(self, method, action, data=None, query="", **kw):
        rf = APIRequestFactory()
        path = f"/api/pre-event-docs/{action}/{query}"
        req = (getattr(rf, method)(path, data, format="json")
               if data is not None else getattr(rf, method)(path))
        force_authenticate(req, user=self.user)
        return PreEventDocsViewSet.as_view({method: action})(req, **kw)

    def test_cancelled_is_on_name_badges_and_off_additional(self):
        """
        THE ASYMMETRY, ASSERTED IN BOTH DIRECTIONS, because it is the one place
        the two lists deliberately disagree.

        Name Badges is the whole roster and keeps a cancelled booking. Additional
        Name Badges lists badges to PRINT, and nobody prints one for somebody who
        is not coming.
        """
        cancelled_inv = BookEvent.objects.create(
            invoice_number="PED-CAN", event_code=CODE, payment_status="Cancelled")
        gone = self.add("Gone", invoice=cancelled_inv, company="Acme Ltd")
        coming = self.add("Coming", company="Borax Ltd")

        badges = services.name_badges(self.rows())
        self.assertEqual({b["delegate_id"] for b in badges}, {gone.id, coming.id},
                         "Name Badges keeps the cancelled booking")

        # Freeze both, then nothing should be outstanding.
        for d in (gone, coming):
            BadgeIssue.objects.create(
                delegate=d, event_code=CODE, name=d.full_name,
                company=d.company_display, issued_by=self.user)
        additional, cancellations = self._changes()
        self.assertEqual(additional, [])
        self.assertEqual(cancellations, [],
                         "a cancelled booking is not a badge to pull, it is "
                         "deliberately still on the list")

    def test_a_renamed_placeholder_that_is_cancelled_asks_for_no_badge(self):
        """
        THE CASE REPORTED FROM REAL USE. A booking sat as TBA, so it was off the
        badge list. Somebody gave it a real name while its payment status was
        Cancelled, and it arrived on Additional Name Badges as a New Badge to
        print for a person who had cancelled.
        """
        cancelled_inv = BookEvent.objects.create(
            invoice_number="PED-TBA", event_code=CODE, payment_status="Cancelled")
        anchor = self.add("Anchor", company="Acme Ltd")
        BadgeIssue.objects.create(
            delegate=anchor, event_code=CODE, name=anchor.full_name,
            company="Acme Ltd", issued_by=self.user)

        placeholder = self.add("TBA", last="", invoice=cancelled_inv,
                               company="Northwind Ltd")
        self.assertEqual(self.additional(), {}, "a placeholder asks for nothing")

        placeholder.first_name, placeholder.last_name = "Priya", "Nair"
        placeholder.save()
        self.assertEqual(self.additional(), {},
                         "renamed, but cancelled, so still no badge to print")
        self.assertIn(placeholder.id,
                      [b["delegate_id"] for b in services.name_badges(self.rows())],
                      "and it IS on Name Badges, which keeps cancelled bookings")

        # Uncancel it and the badge becomes due.
        placeholder.delegate_payment_status = "Paid"
        placeholder.save()
        self.assertEqual(self.additional()[placeholder.id]["remark"], "New Badge")

    def test_a_cancellation_never_appears_on_the_additional_list(self):
        """
        The two lists are two jobs and must not bleed into each other. A badge to
        pull off the table is not an additional name badge.
        """
        staying = self.add("Staying", company="Acme Ltd")
        going = self.add("Going", company="Borax Ltd")
        for d in (staying, going):
            BadgeIssue.objects.create(
                delegate=d, event_code=CODE, name=d.full_name,
                company=d.company_display, issued_by=self.user)

        going.delete()
        additional, cancellations = self._changes()
        self.assertEqual([r["name"] for r in cancellations], ["Going Jones"])
        self.assertEqual(additional, [], "nothing to print, only something to pull")
        for row in additional:
            self.assertNotEqual(row["remark"], "Remove")

    def test_the_diff_is_against_the_frozen_list_not_the_check_in_sheet(self):
        """
        THE BUG THIS PINS, reported from real use: 31 entries after a freeze
        where only one booking had changed.

        Name Badges is every booking; the Check-In Sheet is a filtered subset.
        Freezing the first and diffing against the second reports the difference
        between the two POPULATIONS as if it were change. On the reported event
        that was 46 frozen against 19 at the desk, so thirty perfectly good
        badges read as cancelled.
        """
        unpaid_inv = BookEvent.objects.create(
            invoice_number="PED-UP", event_code=CODE, payment_status="Refunded")
        paid = self.add("Paid", company="Acme Ltd")
        unpaid = self.add("Refunded", invoice=unpaid_inv, company="Borax Ltd")
        excluded = self.add("Table", company="Crest Ltd", code="Speaker Table")

        # Freeze the NAME BADGE list, which is what the page freezes.
        badges = services.name_badges(self.rows())
        self.assertEqual(len(badges), 3, "all three are on the badge list")
        for b in badges:
            BadgeIssue.objects.create(
                delegate_id=b["delegate_id"], event_code=CODE,
                name=b["name"], company=b["company"], issued_by=self.user)

        # Only one of the three reaches the desk sheet.
        self.assertEqual(len(services.check_in(self.rows())), 1)

        additional, cancellations = self._changes()
        self.assertEqual(additional, [], "nothing changed, so nothing to print")
        self.assertEqual(cancellations, [],
                         "non-paying and excluded bookings are NOT cancellations")

        # One real change, and exactly one row.
        unpaid.company_name_raw = "Borax Holdings Ltd"
        unpaid.save()
        additional, cancellations = self._changes()
        self.assertEqual(len(additional), 1)
        self.assertEqual(additional[0]["delegate_id"], unpaid.id)
        self.assertEqual(additional[0]["remark"], "Company Change")
        self.assertEqual(cancellations, [])

    def test_the_whole_page_is_one_query_over_the_delegates(self):
        for i in range(25):
            self.add(f"P{i}", company=f"Firm {i % 5} Ltd")

        # THE POINT OF THIS TEST. Five tabs, five queries, and the count does
        # not move when a tab is added, because every list is a reshaping of one
        # fetch. A later change that turns a projection back into a queryset
        # shows up right here.
        #
        # The five, and why each is its own: the delegates, the events catalogue
        # row the change deadline is computed from, the badge log the change
        # lists diff against, the stored networking plan, and the run history
        # aggregate. Nothing here is an N+1; the delegate query select_relateds
        # both the invoice and the company.
        with self.assertNumQueries(5):
            res = self.call("get", "docs", query=f"?event_code={CODE}")

        self.assertEqual(res.status_code, 200)
        # The four reports, keyed as the spec names them.
        self.assertEqual(len(res.data["name_badges"]), 25)
        self.assertEqual(len(res.data["check_in"]), 25)
        self.assertIn("additional", res.data)
        self.assertIn("cancellations", res.data)
        self.assertIn("networking", res.data)
        self.assertNotIn("registered", res.data, "Registered Delegates is gone")
        self.assertEqual(res.data["change_window_days"], 14)

    def test_the_payload_carries_the_event_detail_the_picker_shows(self):
        self.add("Anyone")
        res = self.call("get", "docs", query=f"?event_code={CODE}")
        for key in ("event_name", "event_date", "location", "upcoming_events"):
            self.assertIn(key, res.data, f"the picker reads {key}")
        self.assertEqual(res.data["event_name"], "Pre-Event Docs Test")

    def test_the_event_list_carries_the_same_detail_per_row(self):
        self.add("Anyone")
        res = self.call("get", "events")
        row = next(r for r in res.data if r["event_code"] == CODE)
        for key in ("event_name", "event_date", "location", "delegates"):
            self.assertIn(key, row, f"the picker reads {key} per row")

    def test_docs_needs_an_event_code(self):
        self.assertEqual(self.call("get", "docs").status_code, 400)

    def test_a_badge_run_records_what_was_printed_not_what_the_row_says_now(self):
        d = self.add("Printed", company="As Printed Ltd")
        res = self.call("post", "badge_run", {
            "event_code": CODE,
            "badges": [{"delegate_id": d.id, "name": "As Printed",
                        "company": "As Printed Ltd"}],
        })
        self.assertEqual(res.status_code, 201)

        # The row changes afterwards; the log must not follow it.
        d.first_name, d.last_name = "Since", "Renamed"
        d.save()
        badge = BadgeIssue.objects.get(delegate=d)
        self.assertEqual(badge.name, "As Printed")
        self.assertEqual(self.to_print_change(d), "Name Change")

    def to_print_change(self, delegate):
        rows, _ = services.badge_changes(self.rows(), CODE,
                                         event_date=self.event.event_date)
        return next(r["remark"] for r in rows
                    if r["delegate_id"] == delegate.id)

    def test_undo_removes_exactly_one_run(self):
        a, b = self.add("A"), self.add("B")
        first = self.call("post", "badge_run", {
            "event_code": CODE,
            "badges": [{"delegate_id": a.id, "name": a.full_name, "company": "Acme Ltd"}],
        }).data["run_id"]
        self.call("post", "badge_run", {
            "event_code": CODE,
            "badges": [{"delegate_id": b.id, "name": b.full_name, "company": "Acme Ltd"}],
        })
        self.assertEqual(BadgeIssue.objects.count(), 2)

        res = self.call("delete", "undo_badge_run", run_id=str(first))
        self.assertEqual(res.status_code, 200)
        self.assertEqual([x.delegate_id for x in BadgeIssue.objects.all()], [b.id])

    def test_the_table_count_asked_for_is_the_table_count_used(self):
        """
        THE BUG THIS PINS. The field used to be people per table, so asking for
        six produced 28 tables on a 170 person event. Six means six tables.
        """
        for i in range(30):
            self.add(f"N{i}", company=f"Firm {i % 6} Ltd")

        res = self.call("post", "draw", {"event_code": CODE, "tables": 6, "rounds": 3})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["tables"], 6, "the room has six tables, not a derived count")
        self.assertEqual(res.data["attendees"], 30)
        self.assertEqual(res.data["largest_table"], 5)
        self.assertEqual(len(res.data["rosters"]), 3)
        for rnd in res.data["rosters"]:
            self.assertEqual(len(rnd), 6, "every round uses all six tables")
            self.assertEqual(sum(len(p) for p in rnd.values()), 30)

    def test_people_per_table_is_an_input_of_its_own(self):
        """
        The second field. Sent alone, the table count follows from it; sent
        with a count, the count wins and the seats are a ceiling.
        """
        for i in range(30):
            self.add(f"N{i}", company=f"Firm {i % 6} Ltd")

        res = self.call("post", "draw", {"event_code": CODE, "per_table": 8})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["tables"], 4, "thirty people at eight a table")
        self.assertLessEqual(res.data["largest_table"], 8)

        res = self.call("post", "draw",
                        {"event_code": CODE, "tables": 6, "per_table": 8})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["tables"], 6, "the count still wins")
        self.assertEqual(res.data["largest_table"], 5)

    def test_a_room_that_cannot_seat_everybody_is_refused_helpfully(self):
        """
        Not a 500, and not a silent reseat. The message names both numbers that
        would work, because a bare refusal leaves the desk nowhere to go.
        """
        for i in range(30):
            self.add(f"N{i}")
        res = self.call("post", "draw",
                        {"event_code": CODE, "tables": 2, "per_table": 4})
        self.assertEqual(res.status_code, 400)
        self.assertIn("8 tables", res.data["detail"])
        self.assertIn("15 per table", res.data["detail"])

        self.assertEqual(
            self.call("post", "draw",
                      {"event_code": CODE, "per_table": 999}).status_code, 400)

    def test_the_page_is_told_the_head_count_it_needs(self):
        """
        networking_attendees ties the two fields together on screen before any
        draw exists. It is the DESK population, not the badge list.
        """
        for i in range(5):
            self.add(f"N{i}")
        self.add("Gone", delegate_payment_status="Cancelled")
        res = self.call("get", "docs", {"event_code": CODE})
        self.assertEqual(res.data["networking_attendees"], 5)
        self.assertEqual(len(res.data["name_badges"]), 6, "cancelled keeps its badge")

    def test_a_draw_rejects_an_impossible_table_count(self):
        for i in range(10):
            self.add(f"N{i}")
        self.assertEqual(
            self.call("post", "draw", {"event_code": CODE, "tables": 999}).status_code, 400)
        self.assertEqual(
            self.call("post", "draw", {"event_code": CODE, "rounds": 99}).status_code, 400)

    def test_more_tables_than_people_is_capped_rather_than_refused(self):
        for i in range(4):
            self.add(f"N{i}")
        res = self.call("post", "draw", {"event_code": CODE, "tables": 20})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["tables"], 4, "an empty table is not a seating")

    def test_a_draw_with_nobody_registered_is_refused(self):
        self.assertEqual(
            self.call("post", "draw", {"event_code": "NOBODY"}).status_code, 400)
