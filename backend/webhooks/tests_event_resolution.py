"""
webhooks/tests_event_resolution.py
──────────────────────────────────
Anchored boundary matching for inbound event codes.

THE BUG: a payload carrying "BIU" attached to `BIUK - PM`, because resolution
used istartswith/icontains with no boundary check and -event_date picked the
winner. The one hard requirement these tests exist to hold is that a code
followed by an alphanumeric character never matches.

The trailing segment of an event code is dynamic, so Set 4 parametrises over
shapes rather than asserting against the two codes that happen to exist today.
"""
from datetime import date

from django.db import transaction
from django.test import TestCase
from django.urls import reverse

from book_delegate.models import BookDelegate
from book_event.models import BookEvent
from events.models import Event
from webhooks.event_resolver import Outcome, resolve_event_code
from webhooks.models import WebhookApiKey


def make_event(code, *, web_bookings, event_date=date(2026, 2, 11), name=None):
    """
    Event.save() derives accepting_web_bookings from web_bookings and derives
    name from official_event_name — setting accepting_web_bookings directly is
    silently overwritten, so fixtures must go through web_bookings.
    """
    return Event.objects.create(
        event_code          = code,
        official_event_name = name or f"Event {code}",
        event_date          = event_date,
        web_bookings        = web_bookings,
    )


def resolve(raw):
    """Resolve exactly as the processor does: raw plus its normalised form."""
    from webhooks.services import WebhookProcessor
    from webhooks.models import WebhookLog
    proc = WebhookProcessor(WebhookLog(payload={}))     # unsaved; no DB write
    return resolve_event_code(raw, proc.normalize_event_code(raw),
                              for_web_booking=True)


class ResolverSet1Tests(TestCase):
    """Fixtures: BIU/GS - PM (ON), BIUK - PM (ON), BIUK - PM26 (OFF)."""

    @classmethod
    def setUpTestData(cls):
        cls.biu_gs = make_event("BIU/GS - PM",  web_bookings=True,
                                event_date=date(2026, 2, 9))
        cls.biuk   = make_event("BIUK - PM",    web_bookings=True,
                                event_date=date(2026, 2, 11))
        cls.biuk26 = make_event("BIUK - PM26",  web_bookings=False,
                                event_date=date(2026, 3, 1))

    def test_biu_does_not_select_biuk(self):
        """The reported bug. 'BIU' must never reach `BIUK - PM`."""
        r = resolve("BIU")
        self.assertEqual(r.outcome, Outcome.BOUNDARY)
        self.assertEqual(r.event.event_code, "BIU/GS - PM")
        self.assertNotEqual(r.event.event_code, "BIUK - PM")
        self.assertNotIn("BIUK - PM", r.matched_codes)
        # BIUK - PM was offered by the prefilter and rejected by the rule —
        # that is the difference the fix makes, so assert it explicitly.
        self.assertIn("BIUK - PM", r.candidates)

    def test_biuk_resolves_to_biuk(self):
        r = resolve("BIUK")
        self.assertEqual(r.event.event_code, "BIUK - PM")

    def test_biu_gs_resolves(self):
        r = resolve("BIU/GS")
        self.assertEqual(r.event.event_code, "BIU/GS - PM")

    def test_case_insensitive(self):
        r = resolve("biuk - pm")
        self.assertEqual(r.outcome, Outcome.EXACT)
        self.assertEqual(r.event.event_code, "BIUK - PM")

    def test_bookings_off_is_distinct_from_no_match(self):
        """
        Raw-exact hits BIUK - PM26, which is closed. The tier wins outright: it
        must NOT fall through to normalised-exact and quietly book onto the open
        BIUK - PM edition.
        """
        r = resolve("BIUK - PM26")
        self.assertEqual(r.outcome, Outcome.BOOKINGS_OFF)
        self.assertIsNone(r.event)
        self.assertEqual(r.http_status, 400)
        self.assertIn("BIUK - PM26", r.matched_codes)
        self.assertNotEqual(r.outcome, Outcome.NO_MATCH)
        self.assertIn("web bookings is disabled", r.error_message)

    def test_unknown_code_is_no_match(self):
        r = resolve("ZZZ")
        self.assertEqual(r.outcome, Outcome.NO_MATCH)
        self.assertIsNone(r.event)
        self.assertEqual(r.http_status, 400)
        self.assertIn("anchored boundary matching", r.error_message)


class ResolverSet2Tests(TestCase):
    """Single fixture BIUK - PM (ON): 'BIU' has nothing legitimate to match."""

    @classmethod
    def setUpTestData(cls):
        cls.biuk = make_event("BIUK - PM", web_bookings=True)

    def test_biu_alone_is_no_match(self):
        r = resolve("BIU")
        self.assertEqual(r.outcome, Outcome.NO_MATCH)
        self.assertIsNone(r.event)
        self.assertEqual(r.http_status, 400)
        # The prefilter DID offer it; the boundary rule is what rejected it.
        self.assertIn("BIUK - PM", r.candidates)
        self.assertEqual(r.matched_codes, [])


class ResolverSet3Tests(TestCase):
    """Two open editions, both boundary-matching: refuse to guess."""

    @classmethod
    def setUpTestData(cls):
        cls.a = make_event("BIU - PM", web_bookings=True,
                           event_date=date(2026, 2, 9))
        cls.b = make_event("BIU - RS", web_bookings=True,
                           event_date=date(2027, 2, 9))   # later: the old tiebreak

    def test_ambiguous_is_409_and_never_tiebroken(self):
        r = resolve("BIU")
        self.assertEqual(r.outcome, Outcome.AMBIGUOUS)
        self.assertIsNone(r.event)
        self.assertEqual(r.http_status, 409)
        self.assertCountEqual(r.matched_codes, ["BIU - PM", "BIU - RS"])
        self.assertIn("Disambiguate at source", r.error_message)

    def test_ambiguity_ignores_closed_editions(self):
        """A closed third edition does not make an open single match ambiguous."""
        make_event("BIU - XX", web_bookings=False)
        r = resolve("BIU")
        self.assertEqual(r.outcome, Outcome.AMBIGUOUS)
        self.assertNotIn("BIU - XX", r.matched_codes)


class ResolverSet4BoundaryShapeTests(TestCase):
    """
    The actual requirement, parametrised over dynamic trailing segments.

    Each case gets its own Event inside a savepoint that is rolled back, so the
    codes never collide with each other and no row is deleted.
    """

    RESOLVES = ["BIU - XX", "BIU/AB - PM", "BIU_EU", "BIU.2"]
    REJECTS  = ["BIUK", "BIU9", "BIUX - PM"]

    def _one_case(self, code, expect_resolved):
        sid = transaction.savepoint()
        try:
            make_event(code, web_bookings=True)
            r = resolve("BIU")
            if expect_resolved:
                self.assertEqual(r.event.event_code if r.event else None, code)
                self.assertEqual(r.outcome, Outcome.BOUNDARY)
            else:
                self.assertEqual(r.outcome, Outcome.NO_MATCH)
                self.assertIsNone(r.event)
                self.assertIn(code, r.candidates)   # prefiltered, then rejected
            return r
        finally:
            transaction.savepoint_rollback(sid)

    def test_non_alphanumeric_next_char_resolves(self):
        for code in self.RESOLVES:
            with self.subTest(event_code=code):
                self._one_case(code, expect_resolved=True)

    def test_alphanumeric_next_char_rejected(self):
        for code in self.REJECTS:
            with self.subTest(event_code=code):
                self._one_case(code, expect_resolved=False)

    def test_underscore_is_a_boundary_not_a_word_char(self):
        """
        Guards the choice of [A-Za-z0-9] over \\b: \\b treats '_' as a word
        character, so BIU_EU would not resolve. It must.
        """
        self._one_case("BIU_EU", expect_resolved=True)


class IngestEndpointTests(TestCase):
    """
    End-to-end through POST /api/webhooks/ingest/, so the HTTP status mapping
    and the no-rows-written guarantee are exercised, not just the resolver.
    """

    @classmethod
    def setUpTestData(cls):
        cls.biu_gs = make_event("BIU/GS - PM", web_bookings=True,
                                event_date=date(2026, 2, 9))
        cls.biuk   = make_event("BIUK - PM",   web_bookings=True,
                                event_date=date(2026, 2, 11))
        cls.biuk26 = make_event("BIUK - PM26", web_bookings=False,
                                event_date=date(2026, 3, 1))
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="test-suite", api_key=cls.raw_key)

    def _payload(self, code, invoice="INV-TEST-001"):
        return {
            "InvoiceNumber": invoice,
            "Eventcode":     code,
            "Eventname":     "whatever",
            "Date":          "2026-02-11",
            "InvoiceDate":   "2026-02-01",
            "Discount":      0,
            "PreTaxAmount":  100,
            "TaxAmount":     0,
            "TotalAmount":   100,
            "AddOnsTotalAmount": 0,
            "Delegates": [{
                "FirstName": "Test",
                "LastName":  "Person",
                "Email":     "test.person@example.com",
            }],
        }

    def _post(self, code, invoice="INV-TEST-001"):
        return self.client.post(
            reverse("webhook-ingest"), data=self._payload(code, invoice),
            content_type="application/json", HTTP_X_CRM_API_KEY=self.raw_key,
        )

    def test_biu_routes_to_biu_gs_not_biuk(self):
        resp = self._post("BIU")
        self.assertIn(resp.status_code, (200, 201), resp.content)
        booking = BookEvent.objects.get(invoice_number="INV-TEST-001")
        self.assertEqual(booking.event_code, "BIU/GS - PM")
        self.assertNotEqual(booking.event_code, "BIUK - PM")

    def test_no_match_is_400_and_writes_nothing(self):
        resp = self._post("ZZZ")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(BookEvent.objects.count(), 0)
        self.assertEqual(BookDelegate.objects.count(), 0)

    def test_bookings_off_is_400_and_writes_nothing(self):
        resp = self._post("BIUK - PM26")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(BookEvent.objects.count(), 0)
        self.assertEqual(BookDelegate.objects.count(), 0)

    def test_failure_log_is_diagnosable(self):
        """A 400 must be explainable from the WebhookLog alone."""
        from webhooks.models import WebhookLog
        self._post("ZZZ")
        log = WebhookLog.objects.latest("id")
        self.assertEqual(log.http_status, 400)
        self.assertIn("DIAG-A-NO-MATCH", log.processing_notes)
        self.assertIn("raw code received", log.processing_notes)
        self.assertIn("normalized code", log.processing_notes)
        self.assertIn("prefilter candidates", log.processing_notes)

    def test_bookings_off_log_names_its_own_rule(self):
        from webhooks.models import WebhookLog
        self._post("BIUK - PM26")
        log = WebhookLog.objects.latest("id")
        self.assertIn("DIAG-B-BOOKINGS-OFF", log.processing_notes)
        self.assertNotIn("DIAG-A-NO-MATCH", log.processing_notes)


class AmbiguousIngestTests(TestCase):
    """409 must survive the view's status mapping rather than becoming a 500."""

    @classmethod
    def setUpTestData(cls):
        make_event("BIU - PM", web_bookings=True, event_date=date(2026, 2, 9))
        make_event("BIU - RS", web_bookings=True, event_date=date(2027, 2, 9))
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="test-suite", api_key=cls.raw_key)

    def test_ambiguous_returns_409_and_writes_nothing(self):
        resp = self.client.post(
            reverse("webhook-ingest"),
            data={
                "InvoiceNumber": "INV-AMB-001", "Eventcode": "BIU",
                "Eventname": "x", "Date": "2026-02-11", "InvoiceDate": "2026-02-01",
                "Discount": 0, "PreTaxAmount": 100, "TaxAmount": 0,
                "TotalAmount": 100, "AddOnsTotalAmount": 0,
                "Delegates": [{"FirstName": "A", "LastName": "B",
                               "Email": "a.b@example.com"}],
            },
            content_type="application/json", HTTP_X_CRM_API_KEY=self.raw_key,
        )
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(BookEvent.objects.count(), 0)
        self.assertEqual(BookDelegate.objects.count(), 0)


class UpdateBookingEventCodeTests(TestCase):
    """
    The double-normalisation fix: an update must write the resolved Event code
    verbatim, never a re-normalised copy of it.
    """

    @classmethod
    def setUpTestData(cls):
        cls.event = make_event("ACU - RS", web_bookings=True)
        cls.raw_key = WebhookApiKey.generate_key()
        WebhookApiKey.objects.create(name="test-suite", api_key=cls.raw_key)

    def _post(self, code, total):
        return self.client.post(
            reverse("webhook-ingest"),
            data={
                "InvoiceNumber": "INV-UPD-001", "Eventcode": code,
                "Eventname": "x", "Date": "2026-02-11", "InvoiceDate": "2026-02-01",
                "Discount": 0, "PreTaxAmount": total, "TaxAmount": 0,
                "TotalAmount": total, "AddOnsTotalAmount": 0,
                "Delegates": [{"FirstName": "A", "LastName": "B",
                               "Email": "a.b@example.com"}],
            },
            content_type="application/json", HTTP_X_CRM_API_KEY=self.raw_key,
        )

    def test_update_path_keeps_the_resolved_code_verbatim(self):
        first = self._post("ACU - RS", 100)
        self.assertIn(first.status_code, (200, 201), first.content)
        self.assertEqual(
            BookEvent.objects.get(invoice_number="INV-UPD-001").event_code,
            "ACU - RS")

        # Second post on the same invoice takes the UPDATE path, which is where
        # the code used to be re-normalised. "ACU" maps to "ACU - RS" and the
        # year-strip rule fires on suffixed codes; neither may touch it now.
        second = self._post("ACU - RS", 200)
        self.assertIn(second.status_code, (200, 201), second.content)
        self.assertEqual(
            BookEvent.objects.get(invoice_number="INV-UPD-001").event_code,
            "ACU - RS")


class BaseCodePlaceholderTests(TestCase):
    """
    The rule itself, on the minimal shape: one closed placeholder, one open
    edition. A row coded exactly `WSU` has base_code `WSU` too, so it is a
    FAMILY placeholder; it exact-matched, it is closed by default, and the tier
    wins outright — so the open edition behind it was never reached.

    These pairs are SYNTHETIC. The real production catalogue, including the two
    families this actually happened to and the one it did not, is
    ProductionCatalogueTests below.
    """

    PAIRS = [("WSU", "WSU - MP"), ("PPTX", "PPTX - JS"), ("BGE", "BGE - AD")]

    def test_closed_placeholder_steps_aside_for_its_open_edition(self):
        for base, edition in self.PAIRS:
            with self.subTest(event_code=base):
                sid = transaction.savepoint()
                try:
                    make_event(base,    web_bookings=False)   # the placeholder
                    make_event(edition, web_bookings=True)    # the real edition
                    r = resolve(base)
                    self.assertEqual(r.outcome, Outcome.BOUNDARY)
                    self.assertEqual(r.event.event_code, edition)
                finally:
                    transaction.savepoint_rollback(sid)

    def test_placeholder_is_recognised_by_base_code_not_by_shape(self):
        """`WSU` is a placeholder because base_code == event_code, nothing else."""
        placeholder = make_event("WSU", web_bookings=False)
        self.assertEqual(placeholder.base_code, "WSU")
        edition = make_event("WSU - MP", web_bookings=True)
        self.assertEqual(edition.base_code, "WSU")

    def test_open_placeholder_still_wins_outright(self):
        """Stepping aside is only for CLOSED placeholders; an open one is a hit."""
        make_event("WSU",      web_bookings=True)
        make_event("WSU - MP", web_bookings=True)
        r = resolve("WSU")
        self.assertEqual(r.outcome, Outcome.EXACT)
        self.assertEqual(r.event.event_code, "WSU")

    def test_placeholder_alone_still_reports_bookings_off(self):
        """No edition to fall through to: the honest answer is still 400."""
        make_event("WSU", web_bookings=False)
        r = resolve("WSU")
        self.assertEqual(r.outcome, Outcome.BOOKINGS_OFF)
        self.assertIsNone(r.event)
        self.assertEqual(r.http_status, 400)

    def test_placeholder_and_closed_edition_reports_both(self):
        """Falling through must not invent a success when everything is shut."""
        make_event("WSU",      web_bookings=False)
        make_event("WSU - MP", web_bookings=False)
        r = resolve("WSU")
        self.assertEqual(r.outcome, Outcome.BOOKINGS_OFF)
        self.assertCountEqual(r.matched_codes, ["WSU", "WSU - MP"])
        self.assertIn("every matched edition", r.error_message)

    def test_real_edition_that_is_closed_is_unchanged(self):
        """
        The load-bearing guarantee this must not weaken: BIUK - PM26 has
        base_code BIUK, so it is an edition, not a placeholder, and it still
        answers "that edition is closed" rather than booking onto BIUK - PM.
        """
        make_event("BIUK - PM",   web_bookings=True)
        make_event("BIUK - PM26", web_bookings=False)
        r = resolve("BIUK - PM26")
        self.assertEqual(r.outcome, Outcome.BOOKINGS_OFF)
        self.assertIsNone(r.event)

    def test_placeholder_never_reaches_a_different_family(self):
        """Falling through uses the boundary rule, so BIU still cannot take BIUK."""
        make_event("BIU",       web_bookings=False)   # closed placeholder
        make_event("BIUK - PM", web_bookings=True)    # different family
        r = resolve("BIU")
        self.assertEqual(r.outcome, Outcome.BOOKINGS_OFF)
        self.assertIsNone(r.event)
        self.assertNotIn("BIUK - PM", r.matched_codes)

    def test_stepping_aside_is_off_by_default(self):
        """
        The regression this flag exists to prevent. paper_review and
        proposal_submission resolve the same catalogue for submissions, where
        every event is web_bookings=False and `.matches` is read directly — so
        without the flag a closed `BIU` placeholder must still win tier 1 alone,
        not widen the set to ['BIU', 'BIU/GS - PM'] and read as ambiguous.
        """
        make_event("BIU",         web_bookings=False)
        make_event("BIU/GS - PM", web_bookings=False)

        default = resolve_event_code("BIU", "BIU")
        self.assertEqual(default.matched_codes, ["BIU"], default.diagnostic)

        booking = resolve_event_code("BIU", "BIU", for_web_booking=True)
        self.assertCountEqual(booking.matched_codes, ["BIU", "BIU/GS - PM"])


class ProductionCatalogueTests(TestCase):
    """
    The three codes from the production report, against the REAL catalogue rows
    they have — not an idealised placeholder/edition pair.

    Two of the three were the placeholder bug; the third never was, and pinning
    that distinction is the point of this class. Read the fixture as the
    screenshot: (event_code, web_bookings).
    """

    CATALOGUE = [
        ("BGE - AD",       False),   # BGE has NO bare placeholder row
        ("PPTX",           False),   # placeholder, shadowing PPTX - JS
        ("PPTX - JS",      True),
        ("PPTX 23",        False),
        ("PPTX 24",        False),
        ("PPTX 25",        False),
        ("FEB2027_WSU-MP", True),    # the only OPEN edition in the WSU family
        ("WSU",            False),   # placeholder
        ("WSU - MP",       False),   # closed, despite looking like the live one
        ("WSU 25",         False),
    ]

    @classmethod
    def setUpTestData(cls):
        for code, open_ in cls.CATALOGUE:
            make_event(code, web_bookings=open_)

    def test_pptx_reaches_its_open_edition(self):
        r = resolve("PPTX")
        self.assertEqual(r.outcome, Outcome.BOUNDARY, r.diagnostic)
        self.assertEqual(r.event.event_code, "PPTX - JS")

    def test_wsu_reaches_the_only_open_edition_in_the_family(self):
        """
        Underscore is a boundary, so FEB2027_WSU-MP is reachable from 'WSU' —
        the [A-Za-z0-9] class over \\b, decided in the module docstring. It is
        also the ONLY open WSU edition: `WSU - MP` is closed.
        """
        r = resolve("WSU")
        self.assertEqual(r.outcome, Outcome.BOUNDARY, r.diagnostic)
        self.assertEqual(r.event.event_code, "FEB2027_WSU-MP")

    def test_bge_was_never_the_placeholder_bug(self):
        """
        No bare `BGE` row exists, so nothing shadowed anything. `BGE - AD` is a
        real edition that is simply closed, and 400 is the correct answer — the
        fix for this one is an admin ticking web bookings, not code.
        """
        r = resolve("BGE")
        self.assertEqual(r.outcome, Outcome.BOOKINGS_OFF, r.diagnostic)
        self.assertEqual(r.matched_codes, ["BGE - AD"])
        self.assertIsNone(r.event)
        self.assertEqual(r.http_status, 400)

    def test_closed_historical_editions_never_create_ambiguity(self):
        """PPTX 23/24/25 and WSU 25 boundary-match but are closed, so they are
        collected and then ignored — one open edition is still unambiguous."""
        for code in ("PPTX", "WSU"):
            with self.subTest(event_code=code):
                r = resolve(code)
                self.assertNotEqual(r.outcome, Outcome.AMBIGUOUS, r.diagnostic)

    def test_an_explicit_historical_edition_still_reports_itself_closed(self):
        """
        'PPTX 25' is an edition, not a placeholder (base_code is PPTX), so tier 1
        wins outright and the operator is told THAT edition is shut rather than
        being silently moved onto PPTX - JS.
        """
        r = resolve("PPTX 25")
        self.assertEqual(r.outcome, Outcome.BOOKINGS_OFF, r.diagnostic)
        self.assertEqual(r.matched_codes, ["PPTX 25"])


class StepAsideAmbiguityTests(TestCase):
    """
    The remaining branch: the placeholder steps aside and finds MORE than one
    open edition. Stepping aside must not become a licence to pick.

    Fixtures use the BIU family this file already resolves everything against —
    these are shapes, not catalogue entries, per the module docstring.
    """

    def test_stepping_aside_onto_two_open_editions_is_409_not_a_guess(self):
        """
        The same 409 "Disambiguate at source" the resolver gives anywhere else,
        and specifically NOT the newest-event_date tiebreak that caused the
        original BIU/BIUK bug — hence the deliberately later date on the second.
        """
        make_event("BIU",      web_bookings=False,
                   event_date=date(2026, 1, 1))          # closed placeholder
        make_event("BIU - PM", web_bookings=True,
                   event_date=date(2026, 9, 14))
        make_event("BIU - RS", web_bookings=True,
                   event_date=date(2027, 9, 14))         # later: the old tiebreak

        r = resolve("BIU")
        self.assertEqual(r.outcome, Outcome.AMBIGUOUS, r.diagnostic)
        self.assertIsNone(r.event)
        self.assertEqual(r.http_status, 409)
        self.assertCountEqual(r.matched_codes, ["BIU - PM", "BIU - RS"])
