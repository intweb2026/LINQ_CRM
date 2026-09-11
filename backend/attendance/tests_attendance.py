"""
attendance/tests_attendance.py
───────────────────────────────
What can actually break at a door, and nothing else.

THE FIXTURES ARE CONFIRMED BY DEFAULT, deliberately. `add()` below books
everybody onto a Paid invoice with an ordinary booking code, so a test about
duplicate prevention or about RBAC is not silently really a test about the
check-in gate. The two tests that DO care about the gate turn it off explicitly.

AN ADMIN HERE IS AN is_all_access TEAM, not role="admin" alone. Both routes are
covered by access.unrestricted(), but the team is what a real administrator on
this system has, and a fixture that only sets the role would pass while the live
account went through the other branch.

THE EVENT CODE CARRIES ITS YEAR ON PURPOSE. "ATT25" is written to the delegate
and BookDelegate.save() strips the trailing digits into `edition`, so the rows
end up as ("ATT", 2025) while the catalogue holds "ATT25". Every test here
therefore crosses the join trap named at the top of pre_event_docs/services.py
rather than side-stepping it with a code that has no year.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import humanize_username
from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event
from teams.models import Team, TeamPermission

from . import qr
from .models import AttendanceRecord

User = get_user_model()

# The delegate-side spelling of each, after save() has moved the year out.
ATT, OTH, EDITION = "ATT", "OTH", 2025


class QrCodecTests(SimpleTestCase):
    """The codec, with no database anywhere near it."""

    def test_round_trip(self):
        token = qr.mint(41, "ATT", "speaker")
        self.assertTrue(token.startswith("LINQ1:"))
        self.assertEqual(qr.read(token),
                         {"type": "speaker", "id": 41, "event": "ATT"})

    def test_a_bare_triple_is_accepted(self):
        """A third-party badge printer cannot compute our HMAC."""
        self.assertEqual(qr.read("delegate:7:ATT"),
                         {"type": "delegate", "id": 7, "event": "ATT"})

    def test_json_in_both_key_styles(self):
        want = {"type": "delegate", "id": 7, "event": "ATT"}
        self.assertEqual(qr.read('{"type":"delegate","id":7,"event":"ATT"}'), want)
        self.assertEqual(qr.read('{"t":"delegate","i":"7","e":"ATT"}'), want)

    def test_an_event_code_may_contain_a_colon(self):
        """split(":", 2), so the rest of the string is the code."""
        self.assertEqual(qr.read("delegate:7:FEB:2027_BIZ-PM")["event"],
                         "FEB:2027_BIZ-PM")

    def test_the_shapes_that_must_be_refused(self):
        for payload in (
            None, "", "   ", "12345",                 # a bare number is not a badge
            "delegate:7",                             # two parts
            "wizard:7:ATT",                           # not a known type
            "delegate:0:ATT", "delegate:-1:ATT",      # non-positive ids
            "delegate:seven:ATT",
            "delegate:7:",                            # no event
            '{"type":"delegate","id":true,"event":"ATT"}',  # True is an int
            "x" * (qr.MAX_LEN + 1),
            "LINQ1:not-a-real-signature",
        ):
            self.assertIsNone(qr.read(payload), repr(payload))

    def test_a_tampered_token_reads_the_same_as_a_wrong_key_one(self):
        token = qr.mint(41, "ATT")
        self.assertIsNone(qr.read(token[:-1] + ("A" if token[-1] != "A" else "B")))

    def test_minting_is_deterministic(self):
        """
        The whole of duplicate prevention for the Pre-Event Docs badge sheet.
        One person on one event has ONE code, so regenerating an event's badges
        produces byte-identical images and a card printed last week still
        matches. If this ever goes back to signing.dumps, that promise is gone
        and nothing else in the codebase would notice.
        """
        self.assertEqual(qr.mint(41, "ATT", "speaker"),
                         qr.mint(41, "ATT", "speaker"))

    def test_the_three_inputs_all_change_the_code(self):
        base = qr.mint(41, "ATT", "delegate")
        self.assertNotEqual(base, qr.mint(42, "ATT", "delegate"))
        self.assertNotEqual(base, qr.mint(41, "OTH", "delegate"))
        self.assertNotEqual(base, qr.mint(41, "ATT", "speaker"))

    def test_a_badge_printed_before_minting_became_deterministic_still_reads(self):
        """
        A badge is a PRINTED artifact, so the old signing.dumps form has to keep
        working while any card from before the change can still turn up at a
        door. See the ponytail note in qr._read_signed for when to drop it.
        """
        from django.core import signing
        legacy = qr.PREFIX + signing.dumps("speaker:41:ATT", salt=qr.SALT)
        self.assertEqual(qr.read(legacy),
                         {"type": "speaker", "id": 41, "event": "ATT"})


class Base(TestCase):
    """Two events, four accounts, and a roster that is confirmed by default."""

    @classmethod
    def setUpTestData(cls):
        cls.all_access = Team.objects.create(name="att_admin", is_all_access=True)
        cls.door_team = Team.objects.create(name="att_door")
        cls.watch_team = Team.objects.create(name="att_watch")
        # view + create: may read the log AND work the door.
        TeamPermission.objects.create(
            team=cls.door_team, module="attendance",
            can_view=True, can_create=True,
        )
        # view only: the supervisor who watches arrivals and cannot record one.
        TeamPermission.objects.create(
            team=cls.watch_team, module="attendance", can_view=True,
        )

        cls.admin = User.objects.create_user(
            username="att_admin", password="x", role="admin",
            email="att_admin@iq-hub.com", team=cls.all_access,
        )
        cls.sca = User.objects.create_user(
            username="att_sca", password="x", role="sales",
            email="att_sca@iq-hub.com", team=cls.door_team,
        )
        cls.viewer = User.objects.create_user(
            username="att_viewer", password="x", role="sales",
            email="att_viewer@iq-hub.com", team=cls.watch_team,
        )
        # Same team as the SCA, so the module grant is identical and only the
        # event assignment differs. This is the account every RBAC test below
        # uses, because it isolates the scope from the permission.
        cls.stranger = User.objects.create_user(
            username="att_stranger", password="x", role="sales",
            email="att_stranger@iq-hub.com", team=cls.door_team,
        )

        # sales_executive, NOT the assigned_events M2M. On the live catalogue
        # that M2M is empty on every event while this column is set on most, and
        # User.visible_event_codes() reads both; a fixture using only the M2M
        # would pass against an access rule that answers "no events" in
        # production. See attendance/access.py.
        # event_date is NOT NULL on Event, and official_event_name rather than
        # `name`: Event.save() overwrites `name` from it.
        when = timezone.localdate() + timedelta(days=30)
        cls.event = Event.objects.create(
            event_code="ATT25", official_event_name="Attendance Test",
            event_date=when, sales_executive=cls.sca,
        )
        cls.other_event = Event.objects.create(
            event_code="OTH25", official_event_name="Some Other Event",
            event_date=when, sales_executive=cls.admin,
        )
        cls.inv = BookEvent.objects.create(
            invoice_number="ATT-001", event_code="ATT25", payment_status="Paid",
        )
        cls.other_inv = BookEvent.objects.create(
            invoice_number="OTH-001", event_code="OTH25", payment_status="Paid",
        )

    @classmethod
    def add(cls, first, last="Jones", company="Acme Ltd", code="Delegate",
            invoice=None, event="ATT25", **kw):
        """One CONFIRMED attendee unless a caller says otherwise."""
        return BookDelegate.objects.create(
            invoice=invoice or cls.inv,
            event_code=event,
            first_name=first, last_name=last,
            company_name_raw=company,
            booking_code=code,
            email=kw.pop("email", None) or f"{first}.{last}@ex.com".lower().replace(" ", ""),
            **kw,
        )

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def scan(self, user, payload, event_code=ATT, edition=EDITION, **extra):
        return self.api(user).post("/api/attendance/scan/", {
            "payload": payload, "event_code": event_code,
            "edition": edition, **extra,
        }, format="json")


class ScanOutcomeTests(Base):
    """The seven outcomes, in the order the view checks them."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.delegate = cls.add("Ada", "Lovelace")
        cls.speaker = cls.add("Grace", "Hopper", code="Speaker")
        cls.elsewhere = cls.add("Alan", "Turing", invoice=cls.other_inv,
                                event="OTH25")

    def test_1_an_unreadable_payload_is_invalid_qr(self):
        resp = self.scan(self.admin, "not a badge at all")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["result"], "invalid_qr")

    def test_2_an_event_that_does_not_exist_is_invalid_event(self):
        resp = self.scan(self.admin, qr.mint(self.delegate.id, ATT),
                         event_code="NOPE")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["result"], "invalid_event")

    def test_3_an_event_this_caller_may_not_work_is_forbidden_event(self):
        resp = self.scan(self.stranger, qr.mint(self.delegate.id, ATT))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["result"], "forbidden_event")

    def test_3_beats_4_so_an_out_of_scope_probe_learns_nothing(self):
        """
        A stranger scanning a REAL id and a nonexistent one must get the same
        answer, or the refusal itself is an existence oracle.
        """
        real = self.scan(self.stranger, qr.mint(self.delegate.id, ATT)).json()
        fake = self.scan(self.stranger, qr.mint(9_999_999, ATT)).json()
        self.assertEqual(real, fake)

    def test_4_a_badge_naming_nobody_is_invalid_qr(self):
        resp = self.scan(self.admin, qr.mint(9_999_999, ATT))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["result"], "invalid_qr")

    def test_5_somebody_on_another_event_is_wrong_event(self):
        resp = self.scan(self.admin, qr.mint(self.elsewhere.id, ATT))
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["result"], "wrong_event")

    def test_5_uses_the_roster_row_and_not_the_code_in_the_payload(self):
        """
        A badge printed before a transfer names the OLD event. The roster is the
        record, so a payload claiming this event for somebody booked elsewhere
        is still wrong_event.
        """
        lying = qr.mint(self.elsewhere.id, ATT)      # payload says ATT
        self.assertEqual(qr.read(lying)["event"], ATT)
        self.assertEqual(self.scan(self.admin, lying).json()["result"],
                         "wrong_event")

    def test_5_names_the_real_event_only_to_somebody_entitled_to_it(self):
        badge = qr.mint(self.elsewhere.id, ATT)
        # The admin may work OTH, so the refusal names it.
        self.assertIn(OTH, self.scan(self.admin, badge).json()["detail"])
        # The SCA may work ATT and not OTH, so it does not.
        detail = self.scan(self.sca, badge).json()["detail"]
        self.assertEqual(detail, "That badge belongs to a different event.")

    def test_7_a_first_scan_is_checked_in_201(self):
        resp = self.scan(self.sca, qr.mint(self.delegate.id, ATT))
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body["result"], "checked_in")
        self.assertEqual(body["record"]["attendee_name"], "Ada Lovelace")
        self.assertEqual(body["record"]["attendee_type"], "delegate")
        self.assertEqual(body["record"]["company_name"], "Acme Ltd")
        self.assertEqual(body["record"]["event_code"], ATT)
        self.assertEqual(body["record"]["edition"], EDITION)
        self.assertEqual(body["record"]["status"], AttendanceRecord.CHECKED_IN)
        self.assertEqual(body["record"]["source"], "qr_scan")
        # checked_in_at must be IN the payload. auto_now_add would have hidden it.
        self.assertTrue(body["record"]["checked_in_at"])
        # A DISPLAY NAME, not the login handle: User.get_full_name falls back to
        # humanize_username for an account with no first/last name, so "att_sca"
        # prints as "Att Sca". Asserted through the helper so the two stay in step.
        self.assertEqual(
            body["record"]["checked_in_by_name"], humanize_username(self.sca.username)
        )

    def test_6_a_second_scan_is_already_checked_in_200_with_the_first_time(self):
        badge = qr.mint(self.delegate.id, ATT)
        first = self.scan(self.sca, badge).json()["record"]
        again = self.scan(self.sca, badge)
        self.assertEqual(again.status_code, 200)
        body = again.json()
        self.assertEqual(body["result"], "already_checked_in")
        self.assertEqual(body["record"]["id"], first["id"])
        # The ORIGINAL arrival time, not the time of the second scan.
        self.assertEqual(body["record"]["checked_in_at"], first["checked_in_at"])
        self.assertEqual(AttendanceRecord.objects.count(), 1)

    def test_a_speaker_is_recorded_as_a_speaker(self):
        resp = self.scan(self.sca, qr.mint(self.speaker.id, ATT, "speaker"))
        self.assertEqual(resp.json()["record"]["attendee_type"], "speaker")

    def test_a_manual_check_in_records_its_source(self):
        resp = self.scan(self.sca, qr.mint(self.delegate.id, ATT),
                         source="manual")
        self.assertEqual(resp.json()["record"]["source"], "manual")


class DuplicatePreventionTests(Base):
    """The constraint, tested at the database rather than through the view."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.delegate = cls.add("Ada", "Lovelace")

    def _record(self, delegate):
        return AttendanceRecord(
            delegate=delegate, event_code=delegate.event_code,
            edition=delegate.edition, attendee_name="x",
        )

    def test_the_database_refuses_a_second_row_for_one_delegate(self):
        self._record(self.delegate).save()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._record(self.delegate).save()

    def test_the_same_person_booked_onto_another_event_may_also_check_in(self):
        """
        A second event is a second book_delegates row, so it is a second
        arrival and not a duplicate.
        """
        twin = self.add("Ada", "Lovelace", invoice=self.other_inv, event="OTH25")
        self._record(self.delegate).save()
        self._record(twin).save()            # must not raise
        self.assertEqual(AttendanceRecord.objects.count(), 2)

    def test_the_view_reports_a_lost_race_as_already_checked_in(self):
        """
        The row is planted first, so the view's own SELECT misses and the INSERT
        is the one that collides -- which is what the second turnstile
        experiences. A 500 here would be the bug.
        """
        self._record(self.delegate).save()
        resp = self.scan(self.sca, qr.mint(self.delegate.id, ATT))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["result"], "already_checked_in")


class ConfirmedRosterGateTests(Base):
    """Both directions of the gate, which is pre_event_docs' check-in sheet."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.confirmed = cls.add("Ada", "Lovelace")
        refunded = BookEvent.objects.create(
            invoice_number="ATT-002", event_code="ATT25",
            payment_status="Refunded",
        )
        cls.unpaid = cls.add("Nobody", "Home", invoice=refunded)
        cls.internal = cls.add("Our", "Own", company="iQ-Hub")
        cls.not_a_person = cls.add("Speaker", "Table", code="Speaker Table")

    def test_a_confirmed_attendee_may_check_in(self):
        self.assertEqual(
            self.scan(self.sca, qr.mint(self.confirmed.id, ATT)).status_code, 201)

    def test_the_three_kinds_of_row_that_may_not(self):
        for delegate in (self.unpaid, self.internal, self.not_a_person):
            resp = self.scan(self.sca, qr.mint(delegate.id, ATT))
            self.assertEqual(resp.status_code, 400, delegate.first_name)
            self.assertEqual(resp.json()["result"], "invalid_qr")
        self.assertEqual(AttendanceRecord.objects.count(), 0)

    def test_the_roster_lists_exactly_the_confirmed_rows(self):
        body = self.api(self.sca).get(
            "/api/attendance/roster/",
            {"event_code": ATT, "edition": EDITION}).json()
        self.assertEqual([r["delegate_id"] for r in body["results"]],
                         [self.confirmed.id])

    def test_qr_token_will_not_mint_for_an_unconfirmed_row(self):
        resp = self.api(self.sca).post("/api/attendance/qr_token/", {
            "event_code": ATT, "edition": EDITION,
            "delegate_id": self.unpaid.id,
        }, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["result"], "invalid_qr")


class BookingRowTests(Base):
    """
    What a check-in does to the booking, and what it must leave alone.

    The brief said a scan must touch nothing outside its own table. The
    instruction since given is that marking somebody present ticks the IN?
    column on the check-in sheet and the Attendance column on Bookings -- which
    are one field, book_delegates.attendance. So exactly that field moves, and
    this test pins every other one.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.delegate = cls.add("Ada", "Lovelace")

    def test_a_check_in_ticks_attendance_and_changes_nothing_else(self):
        before = BookDelegate.objects.filter(pk=self.delegate.pk).values().get()
        self.assertEqual(before["attendance"], BookDelegate.Attendance.PENDING)

        self.assertEqual(
            self.scan(self.sca, qr.mint(self.delegate.id, ATT)).status_code, 201)

        after = BookDelegate.objects.filter(pk=self.delegate.pk).values().get()
        self.assertEqual(after["attendance"], BookDelegate.Attendance.CONFIRMED)
        # `attendance` is the tick and `updated_at` moves with any save.
        # NOTHING else may -- not delegate_count, not the event code that
        # BookDelegate.save() re-derives, not the payment overrides.
        moved = {k for k in before if before[k] != after[k]}
        self.assertEqual(moved, {"attendance", "updated_at"})

    def test_the_pre_event_docs_check_in_sheet_shows_the_tick(self):
        from pre_event_docs import services
        self.scan(self.sca, qr.mint(self.delegate.id, ATT))
        sheet = services.check_in(list(services.event_queryset(ATT, EDITION)))
        row = next(r for r in sheet if r["delegate_id"] == self.delegate.id)
        self.assertTrue(row["checked_in"])
        self.assertEqual(row["attendance"], BookDelegate.Attendance.CONFIRMED)


class RbacTests(Base):
    """Every endpoint, as somebody who holds the module and not the event."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mine = cls.add("Ada", "Lovelace")
        cls.theirs = cls.add("Alan", "Turing", invoice=cls.other_inv,
                             event="OTH25")

    def _seed_log(self):
        for delegate in (self.mine, self.theirs):
            AttendanceRecord.objects.create(
                delegate=delegate, event_code=delegate.event_code,
                edition=delegate.edition, attendee_name=delegate.first_name,
            )

    def test_the_per_event_endpoints_all_refuse_a_stranger(self):
        client = self.api(self.stranger)
        params = {"event_code": ATT, "edition": EDITION}
        for url in ("/api/attendance/roster/", "/api/attendance/summary/"):
            resp = client.get(url, params)
            self.assertEqual(resp.status_code, 403, url)
            self.assertEqual(resp.json()["result"], "forbidden_event", url)
        resp = client.post("/api/attendance/qr_token/",
                           {**params, "delegate_id": self.mine.id}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_a_stranger_sees_no_events_at_all(self):
        self.assertEqual(self.api(self.stranger).get("/api/attendance/events/").json(),
                         [])

    def test_an_sca_sees_only_their_own_events(self):
        body = self.api(self.sca).get("/api/attendance/events/").json()
        self.assertEqual([e["event_code"] for e in body], [ATT])

    def test_an_admin_sees_both(self):
        body = self.api(self.admin).get("/api/attendance/events/").json()
        self.assertEqual({e["event_code"] for e in body}, {ATT, OTH})

    def test_the_log_is_scoped_and_a_foreign_event_code_intersects_to_nothing(self):
        self._seed_log()
        client = self.api(self.sca)
        mine = client.get("/api/attendance/").json()
        self.assertEqual([r["event_code"] for r in mine["results"]], [ATT])
        # A hand-written filter naming somebody else's event. The scope is on the
        # base queryset, so this narrows it rather than replacing it.
        foreign = client.get("/api/attendance/", {"event_code": OTH}).json()
        self.assertEqual(foreign["results"], [])

    def test_a_stranger_reads_an_empty_log_rather_than_every_row(self):
        self._seed_log()
        body = self.api(self.stranger).get("/api/attendance/").json()
        self.assertEqual(body["results"], [])

    def test_an_admin_reads_the_whole_log(self):
        self._seed_log()
        body = self.api(self.admin).get("/api/attendance/").json()
        self.assertEqual(body["count"], 2)

    def test_a_view_only_role_may_read_the_log_but_not_scan(self):
        self._seed_log()
        client = self.api(self.viewer)
        self.assertEqual(client.get("/api/attendance/").status_code, 200)
        resp = client.post("/api/attendance/scan/", {
            "payload": qr.mint(self.mine.id, ATT),
            "event_code": ATT, "edition": EDITION,
        }, format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(AttendanceRecord.objects.count(), 2)

    def test_a_role_without_the_module_reaches_nothing(self):
        nobody = User.objects.create_user(
            username="att_nobody", password="x", role="sales",
            email="att_nobody@iq-hub.com", team=self.watch_team,
        )
        TeamPermission.objects.filter(team=self.watch_team,
                                      module="attendance").update(can_view=False)
        # The team row is cached per user instance, so a fresh client is a fresh
        # resolution of the matrix.
        self.assertEqual(self.api(nobody).get("/api/attendance/").status_code, 403)


class AppendOnlyTests(Base):
    """The log is written by scanning and by nothing else."""

    def test_the_collection_and_the_detail_route_refuse_every_write(self):
        record = AttendanceRecord.objects.create(
            delegate=self.add("Ada", "Lovelace"), event_code=ATT,
            edition=EDITION, attendee_name="Ada Lovelace",
        )
        # The admin holds every right there is, so a 405 here is the ROUTE
        # refusing rather than a permission doing it.
        client = self.api(self.admin)
        self.assertEqual(client.post("/api/attendance/", {}, format="json").status_code, 405)
        detail = f"/api/attendance/{record.id}/"
        self.assertEqual(client.patch(detail, {"status": "x"}, format="json").status_code, 405)
        self.assertEqual(client.put(detail, {"status": "x"}, format="json").status_code, 405)
        self.assertEqual(client.delete(detail).status_code, 405)


class RosterSearchTests(Base):
    """
    Search, and the four ways the obvious implementation of it fails.

    " Burleigh" carries a LEADING SPACE on purpose. The live data holds exactly
    that, which is what makes a concatenate-and-contains match fail: the joined
    string has a double space in it.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.sam = cls.add("Sam", " Burleigh", company="Northwind Ltd")
        cls.other = cls.add("Sam", "Fisher", company="Acme Ltd")
        cls.also = cls.add("Jo", "Burleigh", company="Acme Ltd")

    def _hits(self, term):
        body = self.api(self.sca).get("/api/attendance/roster/", {
            "event_code": ATT, "edition": EDITION, "search": term,
        }).json()
        return {r["delegate_id"] for r in body["results"]}

    def test_the_stored_name_is_collapsed_for_display(self):
        body = self.api(self.sca).get("/api/attendance/roster/", {
            "event_code": ATT, "edition": EDITION, "search": "Northwind",
        }).json()
        self.assertEqual(body["results"][0]["attendee_name"], "Sam Burleigh")

    def test_a_full_name_matches_across_the_two_columns(self):
        self.assertEqual(self._hits("Sam Burleigh"), {self.sam.id})

    def test_the_order_of_the_words_does_not_matter(self):
        self.assertEqual(self._hits("Burleigh Sam"), {self.sam.id})

    def test_doubled_spaces_in_the_term_are_harmless(self):
        self.assertEqual(self._hits("Sam   Burleigh"), {self.sam.id})

    def test_two_words_narrow_rather_than_widen(self):
        one = self._hits("Sam")
        self.assertEqual(one, {self.sam.id, self.other.id})
        two = self._hits("Sam Burleigh")
        self.assertTrue(two < one, f"{two} is not a subset of {one}")

    def test_a_token_may_match_a_different_column_from_its_neighbour(self):
        """AND across tokens, OR across columns: name plus company."""
        self.assertEqual(self._hits("Burleigh Acme"), {self.also.id})


class SummaryTests(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.delegate = cls.add("Ada", "Lovelace")
        cls.speaker = cls.add("Grace", "Hopper", code="Speaker")

    def test_expected_against_arrived(self):
        client = self.api(self.sca)
        params = {"event_code": ATT, "edition": EDITION}

        before = client.get("/api/attendance/summary/", params).json()
        self.assertEqual((before["expected"], before["arrived"],
                          before["outstanding"]), (2, 0, 2))
        self.assertEqual((before["expected_speakers"],
                          before["expected_delegates"]), (1, 1))
        self.assertIsNone(before["last_arrival"])

        self.scan(self.sca, qr.mint(self.delegate.id, ATT))
        after = client.get("/api/attendance/summary/", params).json()
        self.assertEqual((after["expected"], after["arrived"],
                          after["outstanding"]), (2, 1, 1))
        self.assertTrue(after["last_arrival"])


class BadgeCommandTests(Base):
    """
    The testing utility, and the one thing it must never do.

    THE PNG'S BYTES ARE NOT DECODED HERE, and that is a stated gap rather than
    an oversight. Nothing pure-Python in this project DECODES a QR code -- segno
    only writes them, and the app's own decoder is a browser API -- so pinning a
    zbar binding for one assertion would be a system library added to every
    checkout for a test. What IS asserted is the whole of what the door cares
    about: the payload drawn into the image round-trips through attendance.qr,
    which is the same reader scan/ uses, and that payload checks the person in.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.delegate = cls.add("Ada", "Lovelace")
        cls.speaker = cls.add("Grace", "Hopper", code="Speaker")

    def _run(self, out, **over):
        from io import StringIO
        from django.core.management import call_command
        buffer = StringIO()
        options = {"event": ATT, "edition": EDITION, "out": str(out), **over}
        call_command("attendance_badge", stdout=buffer, **options)
        return buffer.getvalue()

    def test_it_writes_a_png_whose_token_the_door_accepts(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "badge.png"
            printed = self._run(out)
            self.assertTrue(out.exists())
            # A real PNG, by its own magic number.
            self.assertEqual(out.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

            token = printed.split("token    : ")[1].strip()
            self.assertEqual(qr.read(token)["id"], self.delegate.id)
            resp = self.scan(self.sca, token)
            self.assertEqual(resp.status_code, 201, resp.content)

    def test_it_writes_nothing_to_the_database(self):
        import tempfile
        from pathlib import Path

        before = (BookDelegate.objects.count(), AttendanceRecord.objects.count())
        with tempfile.TemporaryDirectory() as tmp:
            self._run(Path(tmp) / "badge.png")
        self.assertEqual(
            (BookDelegate.objects.count(), AttendanceRecord.objects.count()),
            before,
        )

    def test_speaker_picks_a_speaker(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            printed = self._run(Path(tmp) / "s.png", speaker=True)
        self.assertIn("Grace Hopper", printed)
        self.assertIn("(speaker)", printed)

    def test_an_event_nobody_is_confirmed_on_refuses_rather_than_inventing_one(self):
        import tempfile
        from django.core.management.base import CommandError
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CommandError):
                self._run(Path(tmp) / "x.png", event=OTH)
