"""
attendance/tests_qr_email.py
───────────────────────────────
eligible_recipients() (confirmed-only, valid-email-only, dedup-on-sent) and the
permission enforcement on qr_email_preview / send_qr_emails.

Reuses tests_attendance.Base for the fixtures (two events, an SCA, a stranger
on the same team but the other event) rather than re-deriving them here.
"""
import json
from datetime import date
from unittest.mock import patch

from .models import AttendanceQrEmailLog
from . import qr_email
from .qr_email import eligible_recipients
from events.models import Event

from .tests_attendance import ATT, EDITION, Base


# Every batch variable, as the review screen would submit them. [FIRST NAME] is
# absent on purpose: it is the one merge field that is per recipient.
VALUES = dict(
    event_name="Attendance Test", venue="The Grand Hotel", city="Dubai",
    event_dates="1\u20133 July 2027", day_one="Thursday 1 July 2027",
    registration_opens="08:00", registration_closes="17:00", start_time="09:30",
    sender_name="Antonio Patino", sender_title="Event Operations",
    event_url="https://iq-hub.com/attendance-test",
    linkedin_url="https://www.linkedin.com/company/iq-hub",
)

# The subset that lives on the event row and pre-fills the form.
ON_EVENT = dict(venue="The Grand Hotel", city="Dubai",
                registration_opens="08:00", registration_closes="17:00",
                start_time="09:30")


def complete_event(event, **overrides):
    """
    Put the event-held variables on the row, so they PRE-FILL the form.

    update() rather than save(): Event.save() runs `if self.location:
    self.venue = self.location`, so a venue written the ordinary way is lost on
    any event carrying a location.
    """
    Event.objects.filter(pk=event.pk).update(**{**ON_EVENT, **overrides})


def sendable(**overrides):
    """The body a send or a preview needs: scope plus every approved variable."""
    return {"event_code": ATT, "edition": EDITION, **VALUES, **overrides}


class EligibleRecipientsTests(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ok = cls.add("Ada", "Lovelace", email="ada@example.com")
        cls.no_email = cls.add("Grace", "Hopper", email="ada@example.com")
        cls.no_email.email = ""
        cls.no_email.save(update_fields=["email"])
        cls.bad_email = cls.add("Alan", "Turing", email="not-an-email")
        cls.unconfirmed = cls.add(
            "Rosalind", "Franklin", email="rosalind@example.com", code="Speaker Table")
        cls.elsewhere = cls.add(
            "Barbara", "Liskov", email="barbara@example.com",
            invoice=cls.other_inv, event="OTH25")

    def test_confirmed_only(self):
        eligible, _ = eligible_recipients(ATT, EDITION)
        ids = {d.id for d in eligible}
        self.assertIn(self.ok.id, ids)
        self.assertNotIn(self.unconfirmed.id, ids)
        self.assertNotIn(self.elsewhere.id, ids)

    def test_no_email_is_skipped(self):
        eligible, skipped = eligible_recipients(ATT, EDITION)
        self.assertNotIn(self.no_email.id, {d.id for d in eligible})
        self.assertEqual(skipped["no_email"], 1)

    def test_invalid_email_is_skipped(self):
        eligible, skipped = eligible_recipients(ATT, EDITION)
        self.assertNotIn(self.bad_email.id, {d.id for d in eligible})
        self.assertEqual(skipped["invalid_email"], 1)

    def test_already_sent_is_skipped_and_deduped(self):
        AttendanceQrEmailLog.objects.create(
            delegate=self.ok, event_code=ATT, edition=EDITION,
            recipient_email=self.ok.email,
            status=AttendanceQrEmailLog.Status.SENT, sent_by=self.sca,
        )
        eligible, skipped = eligible_recipients(ATT, EDITION)
        self.assertNotIn(self.ok.id, {d.id for d in eligible})
        self.assertEqual(skipped["already_sent"], 1)

    def test_a_failed_send_does_not_block_a_retry(self):
        AttendanceQrEmailLog.objects.create(
            delegate=self.ok, event_code=ATT, edition=EDITION,
            recipient_email=self.ok.email,
            status=AttendanceQrEmailLog.Status.FAILED,
            error_message="boom", sent_by=self.sca,
        )
        eligible, _ = eligible_recipients(ATT, EDITION)
        self.assertIn(self.ok.id, {d.id for d in eligible})

    def test_a_duplicate_successful_log_row_is_refused_by_the_database(self):
        from django.db import IntegrityError, transaction

        AttendanceQrEmailLog.objects.create(
            delegate=self.ok, event_code=ATT, edition=EDITION,
            recipient_email=self.ok.email,
            status=AttendanceQrEmailLog.Status.SENT, sent_by=self.sca,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AttendanceQrEmailLog.objects.create(
                    delegate=self.ok, event_code=ATT, edition=EDITION,
                    recipient_email=self.ok.email,
                    status=AttendanceQrEmailLog.Status.SENT, sent_by=self.sca,
                )


class QrEmailEndpointPermissionTests(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.attendee = cls.add("Ada", "Lovelace", email="ada@example.com")
        complete_event(cls.event)

    def test_preview_is_forbidden_for_a_caller_outside_the_event(self):
        resp = self.api(self.stranger).get(
            "/api/attendance/qr_email_preview/",
            {"event_code": ATT, "edition": EDITION})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["result"], "forbidden_event")

    def test_preview_reports_the_count_for_a_permitted_caller(self):
        with patch("attendance.views.gmail_service.is_connected", return_value=False):
            resp = self.api(self.sca).get(
                "/api/attendance/qr_email_preview/",
                {"event_code": ATT, "edition": EDITION})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["eligible_count"], 1)
        self.assertFalse(body["gmail_connected"])

    def test_preview_reports_whether_connecting_is_even_possible(self):
        """
        What routes QrEmailModal to `blocked` instead of `connect`. Offering the
        button on a server with no usable Google configuration showed the user a
        message about environment variables, which is not theirs to fix.
        """
        broken = {"GMAIL_TOKEN_ENCRYPTION_KEY": "not-a-fernet-key"}
        with patch("attendance.views.gmail_service.is_connected", return_value=False),              self.settings(**broken):
            resp = self.api(self.sca).get(
                "/api/attendance/qr_email_preview/",
                {"event_code": ATT, "edition": EDITION})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["gmail_can_connect"])

    def test_the_reason_reaches_admins_and_nobody_else(self):
        """
        An admin reads what is wrong in the modal; an SCA does not. Without the
        split, either the person who can fix it has to open a server log, or
        every user is shown the name of an environment variable.
        """
        broken = {"GMAIL_TOKEN_ENCRYPTION_KEY": "not-a-fernet-key"}
        with patch("attendance.views.gmail_service.is_connected", return_value=False),              self.settings(**broken):
            as_admin = self.api(self.admin).get(
                "/api/attendance/qr_email_preview/",
                {"event_code": ATT, "edition": EDITION}).json()
            as_sca = self.api(self.sca).get(
                "/api/attendance/qr_email_preview/",
                {"event_code": ATT, "edition": EDITION}).json()

        self.assertIn("GMAIL_TOKEN_ENCRYPTION_KEY", as_admin["gmail_config_error"])
        self.assertEqual(as_sca["gmail_config_error"], "")
        # Both are still told that sending is unavailable.
        self.assertFalse(as_admin["gmail_can_connect"])
        self.assertFalse(as_sca["gmail_can_connect"])

    def test_send_is_forbidden_for_a_caller_outside_the_event(self):
        resp = self.api(self.stranger).post(
            "/api/attendance/send_qr_emails/",
            sendable(), format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["result"], "forbidden_event")

    def test_send_refuses_when_gmail_is_not_connected(self):
        with patch("attendance.views.gmail_service.is_connected", return_value=False):
            resp = self.api(self.sca).post(
                "/api/attendance/send_qr_emails/",
                sendable(), format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["result"], "gmail_not_connected")

    def test_send_succeeds_and_logs_when_gmail_is_connected(self):
        with patch("attendance.views.gmail_service.is_connected", return_value=True),              patch("attendance.qr_email.gmail_service.send_email") as send:
            resp = self.api(self.sca).post(
                "/api/attendance/send_qr_emails/",
                sendable(), format="json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["sent"], 1)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            AttendanceQrEmailLog.objects.filter(
                delegate=self.attendee,
                status=AttendanceQrEmailLog.Status.SENT).count(),
            1,
        )


class ErrorClassificationTests(Base):
    """
    What a failed send tells the person who pressed the button.

    THE BUG THIS PINS. Google's 403 for a disabled Gmail API arrives as a
    700-character HttpError carrying the request URL and the same JSON body
    twice. It was rendered verbatim, once per recipient, so two delegates
    filled the results dialog and a real roster would have produced hundreds of
    identical paragraphs. Worse, every one of those sends was doomed
    identically, so the roster was walked to no purpose.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.first = cls.add("Ada", "Lovelace", email="ada@example.com")
        cls.second = cls.add("Alan", "Turing", email="alan@example.com")
        complete_event(cls.event)

    @staticmethod
    def _http_error(reason, message="Gmail API has not been used in project 1"):
        from googleapiclient.errors import HttpError

        class Resp:
            status = 403
            reason = "Forbidden"

        body = json.dumps({"error": {"code": 403, "message": message,
                                     "errors": [{"message": message,
                                                 "domain": "usageLimits",
                                                 "reason": reason}]}})
        return HttpError(Resp(), body.encode(), uri="https://gmail.googleapis.com/x")

    def test_a_disabled_api_is_reported_once_in_plain_words(self):
        error = self._http_error("accessNotConfigured")
        with patch("attendance.views.gmail_service.is_connected", return_value=True), \
             patch("attendance.qr_email.gmail_service.send_email", side_effect=error) as send:
            resp = self.api(self.sca).post(
                "/api/attendance/send_qr_emails/",
                sendable(), format="json")

        body = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Gmail API is not enabled", body["fatal_error"])
        # Stopped on the first, rather than trying both.
        self.assertEqual(send.call_count, 1)
        self.assertEqual(body["failed"], 1)
        # No dump anywhere the user can see it.
        self.assertNotIn("HttpError", body["fatal_error"])
        self.assertNotIn("googleapis.com", body["fatal_error"])

    def test_the_full_dump_is_still_kept_in_the_log_row(self):
        error = self._http_error("accessNotConfigured")
        with patch("attendance.views.gmail_service.is_connected", return_value=True), \
             patch("attendance.qr_email.gmail_service.send_email", side_effect=error):
            self.api(self.sca).post(
                "/api/attendance/send_qr_emails/",
                sendable(), format="json")

        row = AttendanceQrEmailLog.objects.get(status=AttendanceQrEmailLog.Status.FAILED)
        self.assertIn("has not been used in project", row.error_message)

    def test_one_bad_address_does_not_stop_the_rest(self):
        """
        The counterpart. A per-recipient failure is NOT fatal, so everybody
        after it is still attempted.
        """
        error = self._http_error("invalidArgument", "Invalid to header")
        with patch("attendance.views.gmail_service.is_connected", return_value=True), \
             patch("attendance.qr_email.gmail_service.send_email", side_effect=error) as send:
            resp = self.api(self.sca).post(
                "/api/attendance/send_qr_emails/",
                sendable(), format="json")

        body = resp.json()
        self.assertEqual(body["fatal_error"], "")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(body["failed"], 2)

    def test_whoever_was_not_attempted_stays_eligible_for_a_retry(self):
        error = self._http_error("accessNotConfigured")
        with patch("attendance.views.gmail_service.is_connected", return_value=True), \
             patch("attendance.qr_email.gmail_service.send_email", side_effect=error):
            self.api(self.sca).post(
                "/api/attendance/send_qr_emails/",
                sendable(), format="json")

        eligible, _ = eligible_recipients(ATT, EDITION)
        # Both: the one that failed (failures never block a retry) and the one
        # never attempted.
        self.assertEqual(len(eligible), 2)


class EmailTemplateTests(Base):
    """
    The badge email body, built from the approved batch values.

    THE DEGRADATION TESTS THAT USED TO LIVE HERE ARE GONE, and deliberately.
    Every variable is mandatory and the send is refused while one is blank, so
    the clauses that used to rewrite themselves around a missing venue or
    unknown desk hours are dead code. What replaces them is the gate itself,
    asserted in EmailVariableFormTests: nothing unfilled ever reaches here.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.attendee = cls.add("Ada", "Lovelace", email="ada@example.com")

    def compose(self, **overrides):
        return qr_email.compose_email(self.attendee, {**VALUES, **overrides})

    def test_the_subject_is_the_template_line(self):
        subject, _, _ = self.compose()
        self.assertEqual(subject,
                         "Important: Your check-in QR code for Attendance Test")

    def test_the_subject_and_welcome_carry_the_name_not_the_code(self):
        """
        "Full official event name, not the event code" is the merge table's own
        instruction, and the code is the one thing a delegate cannot read.

        Asserted on the subject and the welcome line rather than the whole
        body: the sign-off upper-cases the name, and "ATTENDANCE TEST" contains
        the string "ATT" without the code being anywhere near it.
        """
        subject, html, _ = self.compose()
        self.assertEqual(subject,
                         "Important: Your check-in QR code for Attendance Test")
        self.assertIn("welcoming you to <b>Attendance Test</b>", html)

    def test_every_merge_field_reaches_the_body(self):
        _, html, text = self.compose()
        for body in (html, text):
            self.assertIn("Dear Ada,", body)                      # first name
            self.assertIn("Attendance Test", body)                # event name
            self.assertIn("The Grand Hotel", body)                # venue
            self.assertIn("Dubai", body)                          # city
            self.assertIn("1\u20133 July 2027", body)              # event dates
            self.assertIn("Thursday 1 July 2027", body)           # day one
            self.assertIn("from 08:00", body)                     # reg opens
            self.assertIn("to 17:00", body)                       # reg closes
            self.assertIn("opens at 09:30", body)                 # start time
            self.assertIn("Antonio Patino", body)                 # sender name
            self.assertIn("Event Operations", body)               # sender title
            self.assertIn("iq-hub.com/attendance-test", body)      # event URL
            self.assertIn("linkedin.com/company/iq-hub", body)     # linkedin URL

    def test_the_city_appears_in_both_places_the_merge_table_names(self):
        _, _, text = self.compose()
        self.assertIn("at The Grand Hotel, Dubai on ", text)
        self.assertIn("We look forward to seeing you in Dubai.", text)

    def test_the_qr_rides_inline_in_html_and_is_named_as_a_pdf_in_text(self):
        _, html, text = self.compose()
        self.assertIn(f'src="cid:{qr_email.QR_CID}"', html)
        self.assertIn("attached as a PDF", html)
        # The text part cannot show an image, so it must point at the PDF
        # rather than promise a picture nobody will see.
        self.assertIn("attached as a PDF", text)
        self.assertNotIn("cid:", text)

    def test_no_placeholder_survives_into_a_delegate_facing_body(self):
        """The failure the whole review screen exists to prevent."""
        for part in self.compose():
            self.assertNotIn("[", part)
            self.assertNotIn("]", part)

    def test_free_text_is_escaped_in_the_html_part(self):
        """
        These values are typed into a form and read off imported rows. An
        ampersand breaks the markup and an angle bracket becomes a tag.
        """
        _, html, text = self.compose(venue="Ritz & Carlton <Main>")
        self.assertIn("Ritz &amp; Carlton &lt;Main&gt;", html)
        self.assertNotIn("<Main>", html)
        # The text part is not markup and must stay literal.
        self.assertIn("Ritz & Carlton <Main>", text)

    def test_a_delegate_with_no_first_name_is_not_greeted_as_blank(self):
        nameless = self.add("", "Nobody", email="nobody@example.com")
        _, _, text = qr_email.compose_email(nameless, VALUES)
        self.assertIn("Dear there,", text)


class EventDateRangeTests(Base):
    """
    Both days, month spelled out, because 07/01 is read two different ways on
    the two sides of an Atlantic that this event list spans.
    """
    def test_the_four_shapes_a_range_can_take(self):
        cases = [
            ((date(2027, 7, 1), date(2027, 7, 1)), "1 July 2027"),
            ((date(2027, 7, 1), None), "1 July 2027"),
            ((date(2027, 7, 1), date(2027, 7, 3)), "1\u20133 July 2027"),
            ((date(2027, 6, 30), date(2027, 7, 2)), "30 June \u2013 2 July 2027"),
            ((date(2027, 12, 31), date(2028, 1, 2)),
             "31 December 2027 \u2013 2 January 2028"),
        ]
        for (start, end), expected in cases:
            with self.subTest(start=start, end=end):
                self.assertEqual(qr_email.format_event_dates(start, end), expected)

    def test_no_date_at_all_is_an_empty_string_not_a_crash(self):
        self.assertEqual(qr_email.format_event_dates(None, None), "")


class EmailVariableFormTests(Base):
    """
    The review screen the SCA sees before EVERY batch: every variable, visible,
    pre-filled, editable, mandatory, and never written back to the catalogue.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.attendee = cls.add("Ada", "Lovelace", email="ada@example.com")
        cls.sca.first_name, cls.sca.last_name = "Antonio", "Patino"
        cls.sca.save(update_fields=["first_name", "last_name"])

    def blank_event(self):
        Event.objects.filter(pk=self.event.pk).update(
            venue="", city="", country="", location="",
            registration_opens="", registration_closes="", start_time="")

    def form(self, user=None):
        return self.api(user or self.sca).get(
            "/api/attendance/qr_email_preview/",
            {"event_code": ATT, "edition": EDITION}).json()["fields"]

    def test_every_template_variable_is_offered_for_review(self):
        """
        All ten from the merge table except [FIRST NAME], which is per
        recipient and cannot be a batch value.
        """
        complete_event(self.event)
        self.assertEqual([f["key"] for f in self.form()], [
            "event_name", "venue", "city", "event_dates", "day_one",
            "registration_opens", "registration_closes", "start_time",
            "sender_name", "sender_title", "event_url", "linkedin_url",
        ])

    def test_the_form_is_pre_filled_from_the_event_and_the_caller(self):
        complete_event(self.event)
        values = {f["key"]: f["value"] for f in self.form()}
        self.assertEqual(values["event_name"], "Attendance Test")
        self.assertEqual(values["venue"], "The Grand Hotel")
        self.assertEqual(values["city"], "Dubai")
        self.assertEqual(values["registration_opens"], "08:00")
        # Derived rather than stored, and still pre-filled.
        self.assertTrue(values["event_dates"])
        self.assertTrue(values["day_one"])
        # The sender is whoever is signed in, so replies reach a real person.
        self.assertEqual(values["sender_name"], "Antonio Patino")

    def test_every_field_carries_a_label_and_a_group_for_the_screen(self):
        for field in self.form():
            self.assertTrue(field["label"])
            self.assertIn(field["group"], {"Event", "Schedule", "Sign-off"})
            self.assertTrue(field["help"])

    def test_a_bare_event_pre_fills_blank_rather_than_dropping_the_field(self):
        """
        A blank field still has to appear, or the SCA cannot be asked to fill
        it and cannot see that it is what is holding the send.
        """
        self.blank_event()
        values = {f["key"]: f["value"] for f in self.form()}
        self.assertEqual(len(values), 12)
        for key in ("venue", "city", "registration_opens", "start_time"):
            self.assertEqual(values[key], "")

    def test_a_venue_that_merely_echoes_the_place_pre_fills_blank(self):
        """
        Event.save() fans `location` into city, country and venue alike, so a
        venue equal to the place is not one anybody printed on an agenda.
        Pre-filling it would put "at Dubai, Dubai" in the welcome line.
        """
        self.blank_event()
        Event.objects.filter(pk=self.event.pk).update(venue="Dubai", city="Dubai")
        values = {f["key"]: f["value"] for f in self.form()}
        self.assertEqual(values["venue"], "")
        self.assertEqual(values["city"], "Dubai")

    def test_edits_are_used_for_the_batch_and_never_written_to_the_event(self):
        """The rule this whole change turns on."""
        complete_event(self.event)
        with patch("attendance.views.gmail_service.is_connected", return_value=True), \
             patch("attendance.qr_email.gmail_service.send_email") as send:
            resp = self.api(self.sca).post(
                "/api/attendance/send_qr_emails/",
                sendable(venue="The Ritz", registration_opens="07:30",
                         sender_title="Head of Operations"),
                format="json")

        self.assertEqual(resp.status_code, 200)
        html = send.call_args.args[3]
        self.assertIn("The Ritz", html)
        self.assertIn("from 07:30 to", html)
        self.assertIn("Head of Operations", html)

        # The catalogue is untouched, which is what protects badges and reports.
        self.event.refresh_from_db()
        self.assertEqual(self.event.venue, "The Grand Hotel")
        self.assertEqual(self.event.registration_opens, "08:00")

    def test_the_approved_values_apply_to_every_recipient_in_the_batch(self):
        """
        One set of event values, but each recipient keeps their own greeting
        and their own QR token.
        """
        self.add("Alan", "Turing", email="alan@example.com")
        complete_event(self.event)
        with patch("attendance.views.gmail_service.is_connected", return_value=True), \
             patch("attendance.qr_email.gmail_service.send_email") as send:
            self.api(self.sca).post("/api/attendance/send_qr_emails/",
                                    sendable(venue="The Ritz"), format="json")

        self.assertEqual(send.call_count, 2)
        greetings, tokens = set(), set()
        for call in send.call_args_list:
            html = call.args[3]
            self.assertIn("The Ritz", html)
            greetings.add(html[:40])
            tokens.add(call.kwargs["inline_images"][0]["content"])
        self.assertEqual(len(greetings), 2)
        self.assertEqual(len(tokens), 2)

    def test_sending_is_refused_while_any_variable_is_blank(self):
        """
        Blank on the event AND absent from the payload. A field the SCA leaves
        empty falls back to the event, so only a value missing from both is
        actually missing.
        """
        self.blank_event()
        with patch("attendance.views.gmail_service.is_connected", return_value=True), \
             patch("attendance.qr_email.gmail_service.send_email") as send:
            resp = self.api(self.sca).post(
                "/api/attendance/send_qr_emails/",
                {"event_code": ATT, "edition": EDITION, "venue": "The Ritz"},
                format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["result"], "incomplete")
        self.assertEqual([f["key"] for f in resp.json()["missing_fields"]],
                         ["city", "registration_opens", "registration_closes",
                          "start_time", "sender_title", "event_url",
                          "linkedin_url"])
        send.assert_not_called()

    def test_the_refusal_names_each_field_so_the_screen_can_mark_it(self):
        self.blank_event()
        with patch("attendance.views.gmail_service.is_connected", return_value=True):
            resp = self.api(self.sca).post(
                "/api/attendance/send_qr_emails/",
                {"event_code": ATT, "edition": EDITION}, format="json")
        for field in resp.json()["missing_fields"]:
            self.assertTrue(field["key"])
            self.assertTrue(field["label"])
            # What the screen prints beside the field.
            self.assertTrue(field["reason"])

    def test_a_blank_edit_falls_back_to_the_event_rather_than_erasing_it(self):
        """
        An empty string in the payload means "not supplied", not "clear it".
        Otherwise a field the SCA never touched could blank the merge.
        """
        complete_event(self.event)
        values = qr_email.event_values(ATT, EDITION, self.sca, {"venue": "   "})
        self.assertEqual(values["venue"], "The Grand Hotel")

    def test_an_unknown_key_in_the_payload_is_ignored(self):
        complete_event(self.event)
        values = qr_email.event_values(ATT, EDITION, self.sca,
                                       {"event_code": "HACKED"})
        self.assertEqual(values["event_name"], "Attendance Test")


class EmailSampleTests(Base):
    """
    The preview the SCA confirms. It has to be the real email, because a
    mockup that drifts from the template is worse than no preview at all.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.attendee = cls.add("Ada", "Lovelace", email="ada@example.com")
        complete_event(cls.event)

    def sample(self, user=None, **overrides):
        return self.api(user or self.sca).post(
            "/api/attendance/qr_email_sample/", sendable(**overrides),
            format="json")

    def test_the_preview_is_the_real_template_for_a_real_recipient(self):
        body = self.sample().json()
        self.assertEqual(body["to"], "ada@example.com")
        self.assertEqual(body["name"], "Ada Lovelace")
        subject, html, _ = qr_email.compose_email(self.attendee, VALUES)
        self.assertEqual(body["subject"], subject)
        # Same body, with only the QR swapped for something a browser renders.
        self.assertIn("The Grand Hotel, Dubai", body["html"])
        self.assertIn("registration desk is open from 08:00 to 17:00", body["html"])
        self.assertIn("Dear Ada,", html)

    def test_the_qr_is_that_delegates_own_code_inlined_for_the_browser(self):
        """
        A cid: reference resolves only inside a mail client, so a preview that
        kept it would show the SCA a broken image where the recipient sees a
        QR. It carries the delegate's real token, not a placeholder.
        """
        html = self.sample().json()["html"]
        self.assertNotIn("cid:", html)
        self.assertIn("src=\"data:image/png;base64,", html)

    def test_the_preview_never_leaks_a_credential(self):
        body = self.sample().json()
        self.assertEqual(sorted(body), ["html", "name", "subject", "to"])

    def test_the_preview_is_refused_for_a_caller_outside_the_event(self):
        resp = self.sample(self.stranger)
        self.assertEqual(resp.status_code, 403)

    def test_the_preview_shows_the_edited_values_not_the_event_ones(self):
        """
        What the SCA approves has to be what goes out, so the preview is built
        from the payload rather than re-read from the catalogue.
        """
        html = self.sample(venue="The Ritz").json()["html"]
        self.assertIn("The Ritz", html)
        self.assertNotIn("The Grand Hotel", html)

    def test_previewing_is_refused_while_any_variable_is_blank(self):
        """Nothing on the event and nothing in the payload leaves no value."""
        Event.objects.filter(pk=self.event.pk).update(
            venue="", city="", country="", location="",
            registration_opens="", registration_closes="", start_time="")
        resp = self.api(self.sca).post(
            "/api/attendance/qr_email_sample/",
            {"event_code": ATT, "edition": EDITION}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["result"], "incomplete")

    def test_nothing_to_preview_is_its_own_answer(self):
        AttendanceQrEmailLog.objects.create(
            delegate=self.attendee, event_code=ATT, edition=EDITION,
            recipient_email=self.attendee.email,
            status=AttendanceQrEmailLog.Status.SENT, sent_by=self.sca)
        resp = self.sample()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["result"], "no_recipients")


class SignOffBlockTests(Base):
    """
    The sign-off: name and title, the event in caps with its place and dates,
    then the two links. Everything but the two URLs is merged from data the CRM
    already holds.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.attendee = cls.add("Ada", "Lovelace", email="ada@example.com")

    def compose(self, **overrides):
        return qr_email.compose_email(self.attendee, {**VALUES, **overrides})

    def test_the_block_reads_in_the_approved_order(self):
        _, _, text = self.compose(
            event_name="Direct Lithium Extraction 2026",
            city="Munich, Germany", event_dates="14 - 15 September, 2026",
            sender_name="Sulivan Cruz", sender_title="Senior Conference Advisor")
        tail = text[text.index("Best,"):]
        self.assertEqual(tail.splitlines(), [
            "Best,",
            "",
            "Sulivan Cruz",
            "Senior Conference Advisor",
            "",
            "DIRECT LITHIUM EXTRACTION 2026",
            "Munich, Germany | 14 - 15 September, 2026",
            "",
            "Event Website: https://iq-hub.com/attendance-test",
            "LinkedIn: https://www.linkedin.com/company/iq-hub",
        ])

    def test_the_event_name_is_upper_case_in_the_block_only(self):
        _, html, _ = self.compose(event_name="Attendance Test")
        self.assertIn("<b>ATTENDANCE TEST</b>", html)
        # The welcome line keeps the name as written.
        self.assertIn("<b>Attendance Test</b> at", html)

    def test_both_links_are_anchors_carrying_the_typed_urls(self):
        _, html, _ = self.compose()
        self.assertIn('<a href="https://iq-hub.com/attendance-test">Event Website</a>',
                      html)
        self.assertIn('<a href="https://www.linkedin.com/company/iq-hub">LinkedIn</a>',
                      html)

    def test_the_text_part_spells_the_urls_out(self):
        """A plain-text part has no anchors, so a bare "LinkedIn" links nowhere."""
        _, _, text = self.compose()
        self.assertIn("Event Website: https://iq-hub.com/attendance-test", text)
        self.assertNotIn("<a ", text)


class SignOffUrlValidationTests(Base):
    """
    The two URLs are typed by hand and land in an href in mail sent to hundreds
    of external recipients, so they are checked rather than trusted.
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.attendee = cls.add("Ada", "Lovelace", email="ada@example.com")
        complete_event(cls.event)

    def test_a_usable_url_passes(self):
        for value in ("https://example.com", "http://example.com/a?b=c"):
            with self.subTest(value=value):
                self.assertEqual(qr_email.url_problem(value), "")

    def test_a_dangerous_scheme_is_refused(self):
        """
        The reason this validation exists. A javascript: href in an email is
        inert in most clients and is not something to ship to a mailing list
        regardless, and it is a valid-looking string otherwise.
        """
        for value in ("javascript:alert(1)", "data:text/html,<script>",
                      "file:///etc/passwd"):
            with self.subTest(value=value):
                self.assertIn("http", qr_email.url_problem(value))

    def test_a_bare_domain_is_refused_because_it_resolves_relatively(self):
        self.assertTrue(qr_email.url_problem("www.example.com"))

    def test_blank_reads_as_required_rather_than_invalid(self):
        self.assertEqual(qr_email.url_problem(""), "Required")

    def test_sending_is_refused_when_a_url_is_unusable(self):
        with patch("attendance.views.gmail_service.is_connected", return_value=True), \
             patch("attendance.qr_email.gmail_service.send_email") as send:
            resp = self.api(self.sca).post(
                "/api/attendance/send_qr_emails/",
                sendable(event_url="javascript:alert(1)"), format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual([f["key"] for f in resp.json()["missing_fields"]],
                         ["event_url"])
        send.assert_not_called()

    def test_previewing_is_refused_on_the_same_rule(self):
        resp = self.api(self.sca).post(
            "/api/attendance/qr_email_sample/",
            sendable(linkedin_url="not a url"), format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["missing_fields"][0]["key"], "linkedin_url")

    def test_the_urls_are_never_pre_filled_from_the_event(self):
        """
        Event.website holds event NAMES on 548 of 768 rows, so reading it would
        put a sentence where an href belongs.
        """
        Event.objects.filter(pk=self.event.pk).update(website="Battery Passport Europe")
        values = {f["key"]: f["value"] for f in qr_email.email_form(ATT, EDITION, self.sca)}
        self.assertEqual(values["event_url"], "")
        self.assertEqual(values["linkedin_url"], "")
