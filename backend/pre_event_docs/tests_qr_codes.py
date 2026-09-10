"""
pre_event_docs/tests_qr_codes.py
─────────────────────────────────
The Generate QR Codes export, and the one thing it has to be true about.

THE TEST THAT MATTERS IS test_a_generated_badge_scans_at_the_door. Everything
else here could pass while the feature is useless: a ZIP full of beautifully
rendered badges the scanner rejects is worse than no ZIP, because it fails at a
turnstile with a queue behind it and nothing on the printed card says why. So
one test carries a token from the export's own projection all the way through
/api/attendance/scan/ and asserts the arrival lands. Two apps, two permission
modules, one codec, asserted end to end rather than in halves.

IT TESTS THE PROJECTION AND THE ARCHIVE SEPARATELY, and that split is forced
rather than chosen. There is no pure-Python QR DECODER in this project, so
nothing can read a token back out of a PDF; a test holding only the ZIP could
count files and check their names but never prove one opens a door. So
views.badge_rows is asserted for scannability, and the ZIP is asserted for
covering exactly those rows, once each, under the right names.

THE THIRD CONCERN IS COLLATERAL. This feature reads the check-in sheet, a
projection shared with four other reports, and it is one import away from the
badge issue log that drives Additional Name Badges. Writing into that log would
have been the obvious reuse and would silently empty the corrections report, so
that report is pinned before and after exporting.
"""
import io
import zipfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from attendance import qr
from attendance.models import AttendanceRecord
from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event
from teams.models import Team, TeamPermission

from . import services
from .models import BadgeIssue
from .views import badge_rows

User = get_user_model()

# Carries its year, so BookDelegate.save() strips it into `edition` and every
# assertion below crosses the join trap named at the top of services.py.
CODE, EDITION = "PQR", 2026
URL = "/api/pre-event-docs/qr-codes/"


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.desk_team = Team.objects.create(name="qr_desk")
        cls.all_access = Team.objects.create(name="qr_admin", is_all_access=True)
        # The badge desk. pre_event_docs only, and no attendance at all, which
        # is the realistic shape: whoever prints the badges is not necessarily
        # whoever stands at the door.
        TeamPermission.objects.create(
            team=cls.desk_team, module="pre_event_docs", can_view=True,
        )
        cls.desk = User.objects.create_user(
            username="qr_desk", password="x", role="operations",
            email="qr_desk@iq-hub.com", team=cls.desk_team,
        )
        cls.admin = User.objects.create_user(
            username="qr_admin", password="x", role="admin",
            email="qr_admin@iq-hub.com", team=cls.all_access,
        )

        Event.objects.create(
            event_code="PQR26", official_event_name="QR Codes Test",
            event_date=timezone.localdate() + timedelta(days=30),
        )
        cls.inv = BookEvent.objects.create(
            invoice_number="PQR-001", event_code="PQR26", payment_status="Paid",
        )

    @classmethod
    def add(cls, first, last="Jones", company="Acme Ltd", code="Delegate",
            invoice=None, email=None, **kw):
        """
        One CONFIRMED attendee unless a caller says otherwise.

        `email` is overridable because book_delegates carries a unique
        constraint on (invoice, email, first_name, last_name), so a namesake on
        the same invoice needs its own address to exist at all.
        """
        return BookDelegate.objects.create(
            invoice=invoice or cls.inv,
            event_code="PQR26",
            first_name=first, last_name=last,
            company_name_raw=company,
            booking_code=code,
            email=email or f"{first}.{last}@ex.com".lower().replace(" ", ""),
            **kw,
        )

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def generate(self, user=None):
        return self.api(user or self.desk).get(
            URL, {"event_code": CODE, "edition": EDITION})

    def names(self, resp=None):
        """The entry names in the exported ZIP, in archive order."""
        resp = resp or self.generate()
        self.assertEqual(resp.status_code, 200, resp.content)
        return zipfile.ZipFile(io.BytesIO(resp.content)).namelist()

    def rows(self):
        """The projection the ZIP is built from."""
        return list(badge_rows(CODE, EDITION))


class QrCodeSheetTests(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.delegate = cls.add("Ada", "Lovelace")
        cls.speaker = cls.add("Grace", "Hopper", code="Speaker")
        # NOT on the check-in sheet, one per clause of at_desk().
        refunded = BookEvent.objects.create(
            invoice_number="PQR-002", event_code="PQR26",
            payment_status="Refunded",
        )
        cls.unpaid = cls.add("Nobody", "Home", invoice=refunded)
        cls.internal = cls.add("Our", "Own", company="iQ-Hub")
        cls.not_a_person = cls.add("Speaker", "Table", code="Speaker Table")

    # ── the population ──────────────────────────────────────────────────────

    def test_one_pdf_per_person_on_the_check_in_sheet_and_nobody_else(self):
        self.assertEqual(sorted(self.names()),
                         ["Ada Lovelace.pdf", "Grace Hopper.pdf"])

    def test_the_population_is_the_check_in_sheet_itself(self):
        """
        Not a second opinion about who is coming. If at_desk() changes, this
        follows it, and a test comparing against a hand-written list would not.
        """
        sheet = services.check_in(list(services.event_queryset(CODE, EDITION)))
        self.assertEqual({r["delegate_id"] for r in sheet},
                         {qr.read(row["token"])["id"] for row in self.rows()})

    def test_a_speaker_is_badged_as_a_speaker(self):
        by_id = {qr.read(r["token"])["id"]: r for r in self.rows()}
        self.assertEqual(by_id[self.speaker.id]["attendee_type"], "speaker")
        self.assertEqual(by_id[self.delegate.id]["attendee_type"], "delegate")

    def test_the_archive_is_a_real_zip_of_real_pdfs(self):
        resp = self.generate()
        self.assertEqual(resp["Content-Type"], "application/zip")
        self.assertIn("attachment;", resp["Content-Disposition"])
        self.assertIn("PQR 2026 QR codes.zip", resp["Content-Disposition"])
        # The count the page reports comes from here, not from its own copy of
        # the roster; see PreEventDocsPage.exportQrCodes.
        self.assertEqual(resp["X-Badge-Count"], "2")

        archive = zipfile.ZipFile(io.BytesIO(resp.content))
        self.assertIsNone(archive.testzip())
        for name in archive.namelist():
            body = archive.read(name)
            self.assertTrue(body.startswith(b"%PDF"), name)
            self.assertGreater(len(body), 500, name)

    def test_every_badge_carries_the_person_it_belongs_to(self):
        row = next(r for r in self.rows()
                   if qr.read(r["token"])["id"] == self.delegate.id)
        self.assertEqual(row["name"], "Ada Lovelace")
        self.assertEqual(row["company"], "Acme Ltd")
        # The DELEGATE's own code and edition, which is what the scanner
        # compares against. The request may name an event without its edition.
        self.assertEqual(row["event_code"], CODE)
        self.assertEqual(row["edition"], EDITION)

    # ── the token, and the door ─────────────────────────────────────────────

    def test_a_generated_badge_scans_at_the_door(self):
        """
        THE ONE THAT MATTERS. A token from the export's own projection, posted
        to the attendance scanner, records an arrival for the right person.
        """
        row = next(r for r in self.rows()
                   if qr.read(r["token"])["id"] == self.delegate.id)

        resp = self.api(self.admin).post("/api/attendance/scan/", {
            "payload": row["token"], "event_code": CODE, "edition": EDITION,
        }, format="json")

        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["result"], "checked_in")
        self.assertEqual(resp.json()["record"]["attendee_name"], "Ada Lovelace")
        self.assertEqual(
            AttendanceRecord.objects.get().delegate_id, self.delegate.id)

    def test_a_speaker_badge_scans_too(self):
        row = next(r for r in self.rows()
                   if qr.read(r["token"])["id"] == self.speaker.id)
        resp = self.api(self.admin).post("/api/attendance/scan/", {
            "payload": row["token"], "event_code": CODE, "edition": EDITION,
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["record"]["attendee_type"], "speaker")

    def test_every_badge_names_a_different_person(self):
        rows = self.rows()
        self.assertEqual(len({r["token"] for r in rows}), len(rows))
        self.assertEqual({qr.read(r["token"])["id"] for r in rows},
                         {self.delegate.id, self.speaker.id})

    def test_each_token_is_bound_to_its_event_as_well_as_its_person(self):
        self.assertEqual(qr.read(self.rows()[0]["token"])["event"], CODE)

    # ── duplicates ──────────────────────────────────────────────────────────

    def test_exporting_twice_produces_the_identical_codes(self):
        """
        The whole of duplicate prevention, and it is a property rather than a
        check. Minting is deterministic, so there is no state to consult, no
        run to record and no way for two unequal badges for one person to both
        be in circulation.
        """
        first = [r["token"] for r in self.rows()]
        second = [r["token"] for r in self.rows()]
        self.assertEqual(first, second)
        self.assertEqual(self.names(), self.names())

    def test_one_pdf_per_person_and_never_two_under_one_name(self):
        names = self.names()
        self.assertEqual(len(names), len(set(names)))

    def test_two_people_with_one_name_both_get_a_badge(self):
        """
        "Firstname Lastname.pdf" cannot be taken literally. 30 events in this
        database have two confirmed attendees sharing a first and last name, so
        a literal reading drops one of them out of the export with nothing said.
        """
        twin = self.add("Ada", "Lovelace", company="Other Ltd",
                        email="ada.lovelace.2@ex.com")
        names = sorted(n for n in self.names() if n.startswith("Ada Lovelace"))
        self.assertEqual(names, ["Ada Lovelace 2.pdf", "Ada Lovelace.pdf"])
        # And both are real, distinct badges rather than one file twice.
        ids = {qr.read(r["token"])["id"] for r in self.rows()}
        self.assertIn(twin.id, ids)
        self.assertIn(self.delegate.id, ids)

    def test_a_badge_reprinted_after_a_check_in_is_still_the_same_badge(self):
        before = [r["token"] for r in self.rows()]
        self.api(self.admin).post("/api/attendance/scan/", {
            "payload": before[0], "event_code": CODE, "edition": EDITION,
        }, format="json")
        self.assertEqual([r["token"] for r in self.rows()], before)

    # ── collateral ──────────────────────────────────────────────────────────

    def test_exporting_writes_nothing_at_all(self):
        rows = BookDelegate.objects.filter(event_code=CODE).values()
        before = {r["id"]: r for r in rows}
        self.generate()
        after = {r["id"]: r for r in
                 BookDelegate.objects.filter(event_code=CODE).values()}
        self.assertEqual(before, after)
        self.assertEqual(BadgeIssue.objects.count(), 0)
        self.assertEqual(AttendanceRecord.objects.count(), 0)

    def test_it_does_not_disturb_additional_name_badges(self):
        """
        The badge issue log was the tempting place to record a QR run, and
        writing there would have told this report the badges were already
        printed and emptied it. Pinned in both halves.
        """
        # A FROZEN RUN FIRST, and this is not scene-setting. badge_changes
        # reports nothing at all on an event that has never been frozen, by
        # design, so comparing an untouched event before and after compares two
        # empty lists and proves nothing. My first version of this test did
        # exactly that and its own guard caught it. So one badge is logged under
        # an OLD name, which makes the report say "Name Change", and THAT is the
        # non-empty thing exporting must not disturb.
        BadgeIssue.objects.create(
            delegate=self.delegate, event_code=CODE, edition=EDITION,
            name="Ada Byron", company="Acme Ltd",
        )

        def report():
            rows = list(services.event_queryset(CODE, EDITION))
            return services.badge_changes(rows, CODE, EDITION, event_date=None)

        before = report()
        # A Name Change specifically, not just any rows. It proves the DIFF is
        # live rather than the report merely listing everybody.
        self.assertIn("Name Change", [r["remark"] for r in before[0]])

        self.generate()

        self.assertEqual(report(), before)

    def test_the_rest_of_the_page_still_answers(self):
        """A smoke test on `docs`, because this feature shares its queryset."""
        resp = self.api(self.desk).get("/api/pre-event-docs/docs/",
                                       {"event_code": CODE, "edition": EDITION})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(resp.json()["check_in"]), 2)

    # ── the guards ──────────────────────────────────────────────────────────

    def test_an_event_code_is_required(self):
        resp = self.api(self.desk).get(URL)
        self.assertEqual(resp.status_code, 400)

    def test_the_module_is_required(self):
        outsider = User.objects.create_user(
            username="qr_outsider", password="x", role="sales",
            email="qr_outsider@iq-hub.com",
            team=Team.objects.create(name="qr_none"),
        )
        self.assertEqual(self.generate(outsider).status_code, 403)

    def test_the_badge_desk_needs_no_attendance_rights_to_export(self):
        """
        The desk holds pre_event_docs and nothing else, which is the point of
        the module split. Exporting works; SCANNING still does not.
        """
        self.assertEqual(self.generate(self.desk).status_code, 200)
        token = self.rows()[0]["token"]
        resp = self.api(self.desk).post("/api/attendance/scan/", {
            "payload": token, "event_code": CODE, "edition": EDITION,
        }, format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(AttendanceRecord.objects.count(), 0)

    def test_an_event_nobody_is_confirmed_on_is_refused_rather_than_empty(self):
        """
        An empty ZIP downloads as a file that looks like it worked. The refusal
        says why instead.
        """
        resp = self.api(self.desk).get(URL, {"event_code": "NOSUCH"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("check-in sheet", resp.json()["detail"])
