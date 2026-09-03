"""
proposal_submission/tests_tracker.py
─────────────────────────────────────
The agenda tracker's added columns: the five stored ones, and the five that are
annotated from somewhere else.

WHAT IS WORTH TESTING HERE, AND WHAT IS NOT
The stored fields are plain CharFields, and the contract tests in tests_gaps.py
already assert that the serializer, BUSINESS_FIELDS and the bulk-update
whitelist cover every model field, so they need one round trip and nothing more.

The annotations are the part that can be silently wrong, and each assertion below
pins one decision taken in ProposalSubmissionViewSet._annotate_tracker_context;
the case-insensitive event match, book_delegates rather than book_events as the
join target, and the delegate override winning over the invoice column. Every one
of those returns a plausible-looking value when it breaks, which is exactly the
kind of failure a grid does not report.
"""
import re
from datetime import date
from pathlib import Path

from django.conf import settings as dj_settings

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from proposal_submission.importer import CREATE, classify_rows, map_headers
from proposal_submission.models import ProposalSubmission
from proposal_submission.serializers import DERIVED_FIELDS
from proposal_submission.tests import _Base, make_event
from proposal_submission.views import (
    AGENDA_SLOT_OPTIONS, APPROACH_STATUSES, PANEL_APPROACHED, REVENUE_POSSIBILITY,
    RISK_LEVELS, SPEAKER_SLOT_STATUSES,
)


def make_booking(code, email, *, invoice_number, request_date=None,
                 invoice_date=None, payment_date=None,
                 payment_status="Pending", delegate_payment_date=None,
                 delegate_payment_status=None):
    """One invoice and its single delegate, the shape a booked speaker has."""
    invoice = BookEvent.objects.create(
        invoice_number=invoice_number, event_code=code,
        contact_email="accounts@somewhere.example",   # NOT the speaker
        request_date=request_date, invoice_date=invoice_date,
        payment_date=payment_date, payment_status=payment_status,
    )
    return BookDelegate.objects.create(
        invoice=invoice, event_code=code, first_name="Eli", last_name="Jasso",
        email=email, delegate_payment_date=delegate_payment_date,
        delegate_payment_status=delegate_payment_status,
    )


class TrackerColumnTests(_Base):
    """The five annotated columns."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tracker_event = make_event(
            "TRK - JS", "Tracker Test Summit", event_date=date(2027, 3, 9))
        cls.tracker_event.status = "Upcoming"
        cls.tracker_event.event_management_team = "Priya Raman"
        cls.tracker_event.spex_team = "Tom Weir"
        cls.tracker_event.save()
        cls.assign_events(cls.tracker_event)

        # scope_queryset matches assigned codes with an exact __in, so a row whose
        # event_code differs from the catalogue in CASE, or names no event at all,
        # is invisible to cls.user by design — see access.py. The two tests below
        # that deliberately store such a code therefore have to look through an
        # admin, or they would be asserting the scope rule rather than the
        # annotation.
        from django.contrib.auth import get_user_model
        cls.admin = get_user_model().objects.create_user(
            username="trk_admin", password="x", email="trk@example.com",
            role="admin", team=cls.role,
        )

    def setUp(self):
        self.client.force_authenticate(self.user)

    def row(self, proposal):
        """The serialised row for one proposal, straight off the list endpoint."""
        response = self.client.get(self.LIST, {"search": proposal.email})
        self.assertEqual(response.status_code, 200, response.data)
        results = response.data["results"]
        self.assertEqual(len(results), 1, results)
        return results[0]

    def proposal(self, **over):
        """A stored proposal, written directly so a test can choose the casing."""
        fields = {
            "event_code": "TRK - JS",
            "speaker_name": "Eli Jasso",
            "email": "eli@cicada.example",
            "submission_date": date(2026, 8, 10),
        }
        fields.update(over)
        return ProposalSubmission.objects.create(**fields)

    # ── Event catalogue ───────────────────────────────────────────────────────

    def test_event_date_and_status_come_from_the_catalogue(self):
        row = self.row(self.proposal())
        self.assertEqual(row["event_date"], "2027-03-09")
        self.assertEqual(row["event_status"], "Upcoming")

    def test_the_team_columns_name_the_agenda_and_spex_teams(self):
        """
        The tracker's Production Executive is the event's AGENDA team, which the
        catalogue stores as event_management_team, and its SPEX Manager is
        spex_team. Both mappings were confirmed by the business rather than
        inferred, and this is where a future edit to either would be caught.
        """
        row = self.row(self.proposal())
        self.assertEqual(row["production_executive"], "Priya Raman")
        self.assertEqual(row["spex_manager"], "Tom Weir")

    def test_the_event_match_is_exact_and_that_is_deliberate(self):
        """
        REPLACES an earlier test that asserted __iexact here.

        That version pinned the behaviour which made one 1,000-row page take
        10.3 seconds: UPPER() on both sides of the comparison, which no btree
        index can answer. Exact matching is safe because every write path
        resolves through the event resolver and stores the catalogue's own
        spelling — zero of the 1,877 stored rows differ from their catalogue
        entry by case alone — and because access.py's row scope already compares
        this column with an exact __in, so a case-mismatched row is invisible to
        a scoped user regardless of what this annotation does.

        The consequence is asserted rather than left implicit: a row whose code
        differs only in case reads as having no event, which is honest about a
        row that no scoped user can see in the first place.
        """
        self.client.force_authenticate(self.admin)
        row = self.row(self.proposal(event_code="trk - js",
                                     email="lower@cicada.example"))
        self.assertIsNone(row["event_date"])
        self.assertIsNone(row["event_status"])

        # The canonical spelling, which is what every write path actually stores,
        # resolves normally.
        exact = self.row(self.proposal(event_code="TRK - JS",
                                       email="exact@cicada.example"))
        self.assertEqual(exact["event_date"], "2027-03-09")

    def test_the_booking_match_stays_case_insensitive_on_email(self):
        """
        The OPPOSITE call to event_code above, for the opposite reason: 72 real
        proposal/delegate pairs in this database match only once email case is
        folded, against 449 that match exactly, so an exact comparison would
        silently drop 14% of the bookings this column exists to show. It is made
        fast by book_delegates_event_email_idx rather than by giving up the match.
        """
        make_booking("TRK - JS", "MiXeD@Cicada.Example",
                     invoice_number="INV-CASE",
                     request_date=date(2026, 9, 4), payment_status="Paid")
        row = self.row(self.proposal(email="mixed@cicada.example"))
        self.assertEqual(row["booking_date"], "2026-09-04")
        self.assertEqual(row["booking_status_se"], "Paid")

    def test_unknown_event_code_leaves_the_columns_null(self):
        """
        Null, not an error and not a blank string. A proposal whose code is not
        in the catalogue has no event date, and saying so is the honest answer.
        """
        proposal = self.proposal(event_code="AFS - JS",
                                 email="other@cicada.example")
        ProposalSubmission.objects.filter(pk=proposal.pk).update(
            event_code="NOSUCH")
        self.client.force_authenticate(self.admin)
        row = self.row(proposal)
        self.assertIsNone(row["event_date"])
        self.assertIsNone(row["event_status"])

    # ── Bookings ──────────────────────────────────────────────────────────────

    def test_booking_columns_read_the_delegate_not_the_invoice_contact(self):
        """
        The join is on the PERSON. Every invoice in make_booking carries an
        accounts@ contact email, so a join through book_events.contact_email
        would find nothing at all here.
        """
        make_booking("TRK - JS", "eli@cicada.example", invoice_number="INV-1",
                     request_date=date(2026, 9, 1),
                     invoice_date=date(2026, 9, 3),
                     payment_date=date(2026, 9, 20), payment_status="Paid")
        row = self.row(self.proposal())
        # booked_on is COALESCE(request_date, invoice_date), so the request date wins.
        self.assertEqual(row["booking_date"], "2026-09-01")
        self.assertEqual(row["payment_date"], "2026-09-20")
        self.assertEqual(row["booking_status_se"], "Paid")

    def test_delegate_override_beats_the_invoice_column(self):
        """
        The same COALESCE(NULLIF(override, ''), invoice) rule the Bookings grid
        uses. Reading the invoice's value would show a status the Bookings screen
        does not, on the same person.
        """
        make_booking("TRK - JS", "eli@cicada.example", invoice_number="INV-2",
                     request_date=date(2026, 9, 1),
                     payment_date=date(2026, 9, 20), payment_status="Pending",
                     delegate_payment_date=date(2026, 10, 5),
                     delegate_payment_status="Paid")
        row = self.row(self.proposal())
        self.assertEqual(row["payment_date"], "2026-10-05")
        self.assertEqual(row["booking_status_se"], "Paid")

    def test_booking_match_ignores_email_case(self):
        make_booking("TRK - JS", "ELI@CICADA.EXAMPLE", invoice_number="INV-3",
                     request_date=date(2026, 9, 1), payment_status="Paid")
        row = self.row(self.proposal())
        self.assertEqual(row["booking_date"], "2026-09-01")

    def test_no_booking_leaves_the_columns_null(self):
        """
        The useful signal on this grid; an empty Booking Date beside a confirmed
        slot is a speaker who has not paid.
        """
        row = self.row(self.proposal())
        self.assertIsNone(row["booking_date"])
        self.assertIsNone(row["payment_date"])
        self.assertIsNone(row["booking_status_se"])

    def test_a_booking_on_another_event_is_not_borrowed(self):
        """Correlated on event_code, so last year's booking stays out of it."""
        make_booking("AFS - JS", "eli@cicada.example", invoice_number="INV-4",
                     request_date=date(2025, 1, 1), payment_status="Paid")
        row = self.row(self.proposal())
        self.assertIsNone(row["booking_date"])
        self.assertIsNone(row["booking_status_se"])

    # ── Read-only, sortable, filterable ───────────────────────────────────────

    def test_derived_columns_cannot_be_written(self):
        """
        EVERY derived column, read off DERIVED_FIELDS so a new annotation cannot
        be added without this covering it.

        The booking trio matters most: Bookings owns those three values, and a
        second place to change them would be a second answer to the same
        question. The PATCH returns 200 rather than 400 because a read-only field
        is dropped, not refused — the shared form posts all of its keys on every
        save, including the ones it only displays.
        """
        proposal = self.proposal()
        make_booking("TRK - JS", "eli@cicada.example", invoice_number="INV-RO",
                     request_date=date(2026, 9, 1), payment_status="Paid")
        submitted = {
            "event_date": "1999-01-01", "event_status": "Cancelled",
            "production_executive": "Nobody", "spex_manager": "Nobody",
            "booking_date": "1999-01-01", "payment_date": "1999-01-01",
            "booking_status_se": "Refunded",
        }
        # The submitted set IS the derived set. A mismatch here means this test
        # has gone out of step with the annotations, not that the API is wrong.
        self.assertEqual(sorted(submitted), sorted(DERIVED_FIELDS))

        response = self.client.patch(
            f"{self.LIST}{proposal.pk}/", submitted, format="json")
        self.assertEqual(response.status_code, 200, response.data)

        # Every value still the real source's, not one of the submitted ones.
        self.assertEqual(response.data["event_date"], "2027-03-09")
        self.assertEqual(response.data["event_status"], "Upcoming")
        self.assertEqual(response.data["production_executive"], "Priya Raman")
        self.assertEqual(response.data["spex_manager"], "Tom Weir")
        self.assertEqual(response.data["booking_date"], "2026-09-01")
        self.assertEqual(response.data["booking_status_se"], "Paid")

    def test_no_derived_column_is_mass_writable_either(self):
        """
        Read-only on the detail form is half of it; mass update would be the wider
        version of the same edit.
        """
        response = self.client.get(f"{self.LIST}bulk_update_schema/")
        self.assertEqual(response.status_code, 200)
        for field in DERIVED_FIELDS:
            self.assertNotIn(field, response.data["fields"])

    def test_derived_columns_are_orderable(self):
        """
        The reason these are annotations rather than SerializerMethodFields. A
        method field cannot appear in an ORDER BY, so this would 400, and the
        grid's Event Date header would silently sort the page on screen instead.
        """
        self.proposal()
        self.proposal(event_code="AFS - JS", email="afs@cicada.example")
        # Read from DERIVED_FIELDS rather than retyped, so a sixth annotation
        # cannot be added without this covering it.
        for field in DERIVED_FIELDS:
            with self.subTest(field=field):
                response = self.client.get(self.LIST, {"ordering": field})
                self.assertEqual(response.status_code, 200, response.data)

    def test_derived_columns_are_registered_as_filterable(self):
        """
        Without an entry in filter_spec_fields the grid is denied by default and
        DataTable re-applies the condition in the BROWSER, over the rows already
        fetched, which is the exact bug the comment atop PROPOSAL_COLS records.
        """
        response = self.client.get(f"{self.LIST}filter_schema/")
        self.assertEqual(response.status_code, 200)
        fields = response.data["fields"]
        for field in DERIVED_FIELDS:
            self.assertIn(field, fields)

    def test_filtering_on_a_derived_column_is_done_by_the_database(self):
        self.proposal()
        make_booking("TRK - JS", "eli@cicada.example", invoice_number="INV-5",
                     request_date=date(2026, 9, 1), payment_status="Paid")
        self.proposal(email="unbooked@cicada.example")
        response = self.client.get(self.LIST, {
            "filter_spec": '{"criteria":[{"field":"booking_status_se",'
                           '"op":"is","value":"Paid"}]}',
        })
        self.assertEqual(response.status_code, 200, response.data)
        emails = [r["email"] for r in response.data["results"]]
        self.assertEqual(emails, ["eli@cicada.example"])


class TrackerStoredFieldTests(_Base):
    """The five columns the agenda team actually types into."""

    TRACKER = {
        "panel_approached": "Yes",
        "panel_topic": "Decarbonising short-haul freight",
        "panel_status": "Awaiting chair sign-off",
        "speaker_slot_reoffered": "In Talks",
        "risk_assessment_live": "Medium Risk",
    }

    def setUp(self):
        self.client.force_authenticate(self.user)

    def test_round_trip(self):
        created = self.client.post(
            self.LIST, self.payload(**self.TRACKER), format="json")
        self.assertEqual(created.status_code, 201, created.data)
        for field, value in self.TRACKER.items():
            self.assertEqual(created.data[field], value)

    def test_every_confirmed_option_fits_its_column(self):
        """
        The confirmed lists against the column widths. 'Non Responsive' is the
        longest re-offer value and 'Medium Risk' the longest risk one; a later
        vocabulary that outgrows its varchar would otherwise fail at the database
        during an import commit, rolling back the whole 500-row chunk. Same class
        of failure accounts/import_common.py:column_errors exists to prevent.
        """
        widths = {
            "panel_approached": PANEL_APPROACHED,
            "speaker_slot_reoffered": APPROACH_STATUSES,
            "risk_assessment_live": RISK_LEVELS,
        }
        for field, options in widths.items():
            limit = ProposalSubmission._meta.get_field(field).max_length
            for option in options:
                with self.subTest(field=field, option=option):
                    self.assertLessEqual(len(option), limit)

    def test_each_confirmed_option_round_trips(self):
        """Every value the form can offer, actually saved and read back."""
        for i, option in enumerate(APPROACH_STATUSES):
            with self.subTest(option=option):
                response = self.client.post(self.LIST, self.payload(
                    email=f"reoffer{i}@example.com",
                    speaker_slot_reoffered=option), format="json")
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data["speaker_slot_reoffered"], option)

    def test_a_blank_panel_approached_is_not_a_no(self):
        """
        Why this is a Select over Yes/No and not a checkbox. An unticked box
        would assert "No" on every row the sheet imported empty, and "nobody has
        been approached yet" is a different fact from "we asked and they said no".
        """
        response = self.client.post(self.LIST, self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["panel_approached"], "")

    def test_any_value_is_accepted(self):
        """
        No choices= anywhere in the stack, deliberately; see the model docstring.
        A value outside the guessed vocabulary must not 400, because the real
        vocabulary is not confirmed and the sheet already holds values nobody
        here has seen.
        """
        response = self.client.post(
            self.LIST, self.payload(risk_assessment_live="Catastrophic"),
            format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["risk_assessment_live"], "Catastrophic")


class TrackerHeaderTests(_Base):
    """The importer's side of pasting the tracker sheet."""

    def test_tracker_headers_map_to_the_new_columns(self):
        mapping, unrecognised = map_headers([
            "Panel Approached?", "Panel Status", "Speaker Slot Re-Offerred",
            "Risk Assesment (Live)",
        ])
        self.assertEqual(unrecognised, [])
        self.assertEqual(list(mapping.values()), [
            "panel_approached", "panel_status", "speaker_slot_reoffered",
            "risk_assessment_live",
        ])

    def test_the_bare_sub_headers_are_reported_not_guessed(self):
        """
        REPLACES a test that asserted "Approached?" and "Topic" mapped to the
        panel columns. Both aliases were removed.

        They rested on an unconfirmed guess about a two-row header, and "Topic"
        is a plausible header for a presentation topic in any pasted file. The
        asymmetry is the whole argument: an UNMAPPED header is reported back to
        the user as unrecognised, while a WRONGLY mapped one is silent. Being
        told about a column nobody can place is the recoverable outcome.
        """
        mapping, unrecognised = map_headers(["Approached?", "Topic"])
        self.assertEqual(mapping, {})
        self.assertEqual(unrecognised, ["Approached?", "Topic"])

    def test_the_full_panel_headers_still_map(self):
        """The unambiguous spellings, which is what the removal left behind."""
        mapping, unrecognised = map_headers(
            ["Panel Approached?", "Panel Topic", "Panel Status"])
        self.assertEqual(unrecognised, [])
        self.assertEqual(list(mapping.values()),
                         ["panel_approached", "panel_topic", "panel_status"])

    def test_tracker_spellings_of_existing_columns(self):
        mapping, unrecognised = map_headers([
            "Full Name", "Sponsorship", "Slot Recommendation from MR",
            "Sales Pitch Factor\n(Low score = more commercial)",
        ])
        self.assertEqual(unrecognised, [])
        self.assertEqual(list(mapping.values()), [
            "speaker_name", "sponsorship_status",
            "slot_recommendation_mr", "sales_pitch_factor",
        ])

    def test_added_to_agenda_maps_to_the_checkbox_not_the_prose_column(self):
        """
        The correction worth pinning. This header was mapped to agenda_addition
        on the first pass, which would have stored the word "TRUE" inside the
        session outline and left the checkbox off on every imported row. The two
        are different facts; see the note on the model field.
        """
        mapping, unrecognised = map_headers(["Added to Agenda", "Agenda Addition"])
        self.assertEqual(unrecognised, [])
        self.assertEqual(mapping, {"Added to Agenda": "added_to_agenda",
                                   "Agenda Addition": "agenda_addition"})

    def test_the_two_slot_columns_reach_two_different_fields(self):
        """
        The correction worth pinning, and the second wrong guess this file has
        caught. "Speaking Slot Assignment" was mapped onto agenda_slot on the
        previous pass, reasoning that the sheet has no column literally called
        "Agenda Slot". It is a SEPARATE field: the MRE recommends a slot on the
        paper review and the agenda team assigns one, and collapsing the two
        would have made the disagreement between suggestion and outcome, which is
        the thing the tracker exists to show, unrepresentable.

        "Agenda Slot" stays pointed at the recommendation, because that is what
        the 1,877 rows already stored under that header hold.
        """
        mapping, unrecognised = map_headers([
            "Agenda Slot", "Slot Recommendation by MRE",
            "Speaking Slot Assignment",
        ])
        self.assertEqual(unrecognised, [])
        self.assertEqual(mapping, {
            "Agenda Slot": "agenda_slot",
            "Slot Recommendation by MRE": "agenda_slot",
            "Speaking Slot Assignment": "speaking_slot_assignment",
        })

    def test_derived_columns_are_neither_stored_nor_reported(self):
        """
        Pasting the whole sheet must not produce seven "unrecognised column"
        warnings for columns whose value has another owner. They are dropped, and
        the grid shows the catalogue's and Bookings' values instead.
        """
        mapping, unrecognised = map_headers([
            "Event Date", "Event Status", "Production Executive",
            "SPEX Manager", "Booking Date", "Payment Date",
            "Booking Status by SE",
        ])
        self.assertEqual(mapping, {})
        self.assertEqual(unrecognised, [])

    def test_a_ticked_checkbox_cell_imports_as_true(self):
        """
        Through classify_rows, not just map_headers: added_to_agenda is the one
        non-text column the tracker adds, so it is coerced with the shared as_bool
        rather than falling through the text pass. A blank cell is False; an
        unreadable one is a row ERROR, because "we could not read this" and "not
        on the agenda" are different answers.
        """
        columns = ["Event Code", "Full Name", "Email Address", "Added to Agenda"]
        mapping, _ = map_headers(columns)
        rows = [
            {"Event Code": "AFS - JS", "Full Name": "Yes Person",
             "Email Address": "yes@x.com", "Added to Agenda": "TRUE"},
            {"Event Code": "AFS - JS", "Full Name": "No Person",
             "Email Address": "no@x.com", "Added to Agenda": ""},
        ]
        plan = classify_rows(rows, mapping, self.user, set())
        self.assertEqual([r["classification"] for r in plan],
                         [CREATE, CREATE], plan)
        self.assertIs(plan[0]["_payload"]["added_to_agenda"], True)
        self.assertIs(plan[1]["_payload"]["added_to_agenda"], False)

    def test_a_genuinely_unknown_column_is_still_reported(self):
        """The dropping above is a named exception, not a blanket silence."""
        mapping, unrecognised = map_headers(["Event Date", "Wobble Factor"])
        self.assertEqual(mapping, {})
        self.assertEqual(unrecognised, ["Wobble Factor"])


class MREFieldTests(_Base):
    """
    qc_grade and qc_score are the paper review's OUTPUT, so a proposal reads them
    and never authors them.

    The bridge still WRITING them is covered by
    paper_review/tests_paper_to_proposal.py, which walks FIELD_MAP and asserts
    each target against its source; that path passes them to serializer.save()
    now rather than through the payload, and that test is what catches it if the
    hand-off breaks.
    """

    def setUp(self):
        self.client.force_authenticate(self.user)

    def test_create_ignores_a_submitted_grade_and_score(self):
        response = self.client.post(
            self.LIST, self.payload(qc_grade="A", qc_score=45), format="json")
        # 201, NOT 400. A read-only field is dropped, not refused, and refusing
        # would make the shared form uneditable for everyone: it posts all its
        # keys on every save, including the two it now only displays.
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["qc_grade"], "")
        self.assertIsNone(response.data["qc_score"])

    def test_patch_cannot_change_them(self):
        proposal = ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Held", email="held@x.com",
            qc_grade="B", qc_score=27,
        )
        response = self.client.patch(
            f"{self.LIST}{proposal.pk}/",
            {"qc_grade": "A", "qc_score": 45, "company_name": "Still Editable"},
            format="json")
        self.assertEqual(response.status_code, 200, response.data)
        proposal.refresh_from_db()
        self.assertEqual(proposal.qc_grade, "B")
        self.assertEqual(proposal.qc_score, 27)
        # The rest of the row is untouched by the restriction.
        self.assertEqual(proposal.company_name, "Still Editable")

    def test_they_are_still_readable_and_sortable(self):
        """
        Read-only, not hidden. The agenda team works from the QC grade, and the
        grid sorts on it.
        """
        ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="R", email="r@x.com",
            qc_grade="B", qc_score=27)
        response = self.client.get(self.LIST, {"ordering": "-qc_score"})
        self.assertEqual(response.status_code, 200, response.data)
        row = next(r for r in response.data["results"] if r["email"] == "r@x.com")
        self.assertEqual(row["qc_grade"], "B")
        self.assertEqual(row["qc_score"], 27)


class LinkFieldTests(_Base):
    """
    Anchor markup in a LinkedIn column collapses to the address inside it.

    All 1,876 stored rows held markup rather than a URL; migration 0008 cleaned
    those, and this is the guard that stops the next one arriving through the API.
    """

    ANCHOR = ('<a href= "https://www.linkedin.com/in/paul-louis-kiesow-91aa36237/" '
              'target = "_blank">https://www.linkedin.com/in/paul-louis-kiesow-91aa36237/</a>')
    CLEAN = "https://www.linkedin.com/in/paul-louis-kiesow-91aa36237/"

    def setUp(self):
        self.client.force_authenticate(self.user)

    def test_anchor_markup_is_stored_as_the_address(self):
        response = self.client.post(
            self.LIST, self.payload(linkedin_speaker=self.ANCHOR), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["linkedin_speaker"], self.CLEAN)

    def test_a_plain_url_is_untouched(self):
        response = self.client.post(
            self.LIST, self.payload(linkedin_company=self.CLEAN), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["linkedin_company"], self.CLEAN)

    def test_blank_is_still_allowed(self):
        """The column is blank=True; unwrapping must not make it required."""
        response = self.client.post(
            self.LIST, self.payload(linkedin_speaker="", linkedin_company=""),
            format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["linkedin_speaker"], "")

    def test_an_empty_link_tag_is_a_400(self):
        """
        The one case as_url calls an error: markup is unambiguously an attempt at
        a link, so a tag carrying no address is a real defect rather than someone
        typing prose. Answered as a field error, not swallowed.
        """
        response = self.client.post(
            self.LIST, self.payload(linkedin_speaker='<a name="x">Paul</a>'),
            format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("linkedin_speaker", response.data)


class AddedToAgendaTests(_Base):
    """The checkbox, which is a different fact from the agenda_addition prose."""

    def setUp(self):
        self.client.force_authenticate(self.user)

    def test_it_defaults_to_false(self):
        response = self.client.post(self.LIST, self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIs(response.data["added_to_agenda"], False)

    def test_it_round_trips_as_a_real_boolean(self):
        response = self.client.post(
            self.LIST, self.payload(added_to_agenda=True), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        # `is True`, not truthy: the grid renders it with a boolean cell and the
        # filter sends the strings "true"/"false", so a "True" string here would
        # read correctly on screen and fail to match anything.
        self.assertIs(response.data["added_to_agenda"], True)

    def test_it_is_independent_of_agenda_addition(self):
        """
        A row can carry the session outline without being on the agenda, and be on
        the agenda with no outline written. If these ever shared a column, one of
        those two states would be unrepresentable.
        """
        response = self.client.post(self.LIST, self.payload(
            added_to_agenda=True, agenda_addition=""), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIs(response.data["added_to_agenda"], True)
        self.assertEqual(response.data["agenda_addition"], "")


class SlotColumnTests(_Base):
    """
    The MRE's recommendation and the agenda team's assignment, side by side.

    Two columns over the SAME ten values, so the failure mode is not a bad value;
    it is the two collapsing into one and the disagreement between them
    disappearing.
    """

    def setUp(self):
        self.client.force_authenticate(self.user)

    def test_they_are_stored_independently(self):
        response = self.client.post(self.LIST, self.payload(
            agenda_slot="Day 1, Morning Session",
            speaking_slot_assignment="Day 2, Closing Session"), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["agenda_slot"], "Day 1, Morning Session")
        self.assertEqual(response.data["speaking_slot_assignment"],
                         "Day 2, Closing Session")

    def test_an_assignment_can_exist_without_a_recommendation(self):
        """A manually created proposal has no paper review to recommend anything."""
        response = self.client.post(self.LIST, self.payload(
            agenda_slot="", speaking_slot_assignment="Day 1, Closing Session"),
            format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["agenda_slot"], "")
        self.assertEqual(response.data["speaking_slot_assignment"],
                         "Day 1, Closing Session")

    def test_both_are_editable(self):
        """
        Neither is read-only, unlike the MRE score and grade. Recorded as a
        decision rather than an oversight: the recommendation does arrive from the
        paper review, but nothing asked for it to be blocked, and a value the team
        cannot correct on a row with no review behind it would be worse than one
        they can.
        """
        proposal = ProposalSubmission.objects.create(
            event_code="AFS - JS", speaker_name="Slot", email="slot@x.com",
            agenda_slot="Day 1, Morning Session")
        response = self.client.patch(
            f"{self.LIST}{proposal.pk}/",
            {"agenda_slot": "Day 2, Opening Session",
             "speaking_slot_assignment": "Day 2, Opening Session"},
            format="json")
        self.assertEqual(response.status_code, 200, response.data)
        proposal.refresh_from_db()
        self.assertEqual(proposal.agenda_slot, "Day 2, Opening Session")
        self.assertEqual(proposal.speaking_slot_assignment,
                         "Day 2, Opening Session")

    def test_both_are_orderable(self):
        for field in ("agenda_slot", "speaking_slot_assignment"):
            with self.subTest(field=field):
                response = self.client.get(self.LIST, {"ordering": field})
                self.assertEqual(response.status_code, 200, response.data)


class VocabularyTests(_Base):
    """
    The confirmed picklists, against the columns and against the frontend.

    WHY THE FRONTEND IS PARSED HERE
    The model deliberately carries no choices=, so these lists exist twice, once
    in views.py for the mass-update allow-list and once in constants.js for the
    form. Nothing but this test stops the two drifting, and drift is not cosmetic:
    paper_review/tests_session_options.py records the case where a short option
    list silently REWROTE 941 rows, because a picker renders nothing for a stored
    value it does not recognise and the next save takes the empty selection.
    Same approach and the same skip as that test.
    """

    CONSTANTS = (Path(dj_settings.BASE_DIR).parent
                 / "frontend" / "src" / "lib" / "constants.js")

    # backend constant -> the JS name that must hold the identical list.
    MIRRORED = {
        "PANEL_APPROACHED": PANEL_APPROACHED,
        "RISK_LEVELS": RISK_LEVELS,
        "APPROACH_STATUSES": APPROACH_STATUSES,
        "SPEAKER_SLOT_STATUSES": SPEAKER_SLOT_STATUSES,
        "REVENUE_POSSIBILITY": REVENUE_POSSIBILITY,
        "PAPER_SESSION_OPTIONS": AGENDA_SLOT_OPTIONS,
    }

    # Which column each vocabulary is stored in, for the width check.
    COLUMNS = {
        "panel_approached": PANEL_APPROACHED,
        "risk_assessment_live": RISK_LEVELS,
        # Both read APPROACH_STATUSES: sponsorship status and slot re-offered are
        # the same five-value outreach pipeline, and views.py no longer keeps a
        # per-field alias for it.
        "speaker_slot_reoffered": APPROACH_STATUSES,
        "sponsorship_status": APPROACH_STATUSES,
        "speaker_slot_status": SPEAKER_SLOT_STATUSES,
        "revenue_possibility": REVENUE_POSSIBILITY,
        "agenda_slot": AGENDA_SLOT_OPTIONS,
        "speaking_slot_assignment": AGENDA_SLOT_OPTIONS,
    }

    def js_array(self, name):
        """The values a named constants.js array lists, in declaration order."""
        source = self.CONSTANTS.read_text(encoding="utf-8")
        match = re.search(rf"{name}\s*=\s*\[(.*?)\]", source, re.S)
        self.assertIsNotNone(match, f"{name} not found in constants.js")
        return re.findall(r"'([^']+)'", match.group(1))

    def test_every_value_fits_its_column(self):
        """
        A too-narrow column is not a validation error, it is a psycopg DataError
        inside import_commit's transaction that rolls back the whole 500-row
        chunk. revenue_possibility was widened from 20 to 50 for exactly this;
        "Genuine clasg(INV sent)" is 23 characters.
        """
        for field, options in self.COLUMNS.items():
            limit = ProposalSubmission._meta.get_field(field).max_length
            for option in options:
                with self.subTest(field=field, option=option):
                    self.assertLessEqual(
                        len(option), limit,
                        f"{option!r} is {len(option)} chars, {field} holds {limit}")

    def test_the_frontend_lists_match_the_backend_exactly(self):
        if not self.CONSTANTS.exists():
            self.skipTest("frontend/src not present in this checkout")
        for js_name, expected in self.MIRRORED.items():
            with self.subTest(constant=js_name):
                self.assertEqual(self.js_array(js_name), list(expected))

    def test_the_two_aliased_lists_stay_aliases(self):
        """
        SPONSORSHIP_STATUSES and SLOT_REOFFER_STATUSES are aliases of
        APPROACH_STATUSES in constants.js, so test_the_frontend_lists_match_the
        _backend_exactly checks the one array behind them and cannot see the two
        names.

        That is the hole this closes: convert either alias to its own literal
        array and it silently stops being covered, which is precisely the two
        lists most likely to be un-aliased later. Assert they are still
        assignments to APPROACH_STATUSES, not arrays.
        """
        if not self.CONSTANTS.exists():
            self.skipTest("frontend/src not present in this checkout")
        source = self.CONSTANTS.read_text(encoding="utf-8")
        for name in ("SPONSORSHIP_STATUSES", "SLOT_REOFFER_STATUSES"):
            with self.subTest(constant=name):
                self.assertRegex(
                    source, rf"{name}\s*=\s*APPROACH_STATUSES;",
                    f"{name} is no longer an alias of APPROACH_STATUSES. If it "
                    f"now holds its own list, add it to MIRRORED so the "
                    f"frontend/backend parity check covers it.")

    def test_no_value_is_listed_twice_or_carries_stray_spacing(self):
        """
        A duplicate renders two identical options; a stray space makes a value
        that looks right on screen and matches nothing stored.
        """
        for name, options in self.MIRRORED.items():
            with self.subTest(constant=name):
                self.assertEqual(len(options), len(set(options)))
                for value in options:
                    self.assertEqual(value, value.strip())
                    self.assertNotIn("  ", value)

    def test_agenda_slot_uses_the_paper_reviews_own_vocabulary(self):
        """
        Not a coincidence to be maintained by hand. proposal_bridge.py maps
        PaperReview.session_location_on_agenda straight into agenda_slot, so the
        two columns must offer the same slots or a generated proposal arrives
        holding a value its own form cannot show.
        """
        from paper_review.tests_session_options import EXPECTED
        self.assertEqual(list(AGENDA_SLOT_OPTIONS), EXPECTED)
