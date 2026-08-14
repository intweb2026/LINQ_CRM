"""
paper_review/tests_event_codes.py
───────────────────────────────────
A1 — proving event_codes.py, which was written but never executed.

WHAT IT ACTUALLY DOES (traced, not assumed)
normalise_event_code() collapses whitespace, strips whitespace hugging '-' and
'/', and upper-cases — so all five spacing/case variants in SPACING_VARIANTS
below key to the same string and canonical_matches() finds the one stored
spelling. resolve_paper_event_code() then re-resolves against THAT canonical
string, which the untouched resolver (webhooks/event_resolver.py — protected,
not modified by this pass) answers EXACT. This is why every variant reports
the SAME canonical spelling as stored.

THE BOUNDARY CONSTRAINT — TRACED PRECISELY, NOT ASSUMED
event_codes.py deliberately does not touch webhooks/event_resolver.py's tiered
semantics: "the first search code producing ANY match wins outright" is still in
force underneath the spacing layer. That has one consequence worth stating
exactly, because a paraphrase of it is over-broad:

  * If 'BIU' is NOT itself a catalogue entry (only 'BIU/GS - PM' and
    'BIUK - PM' exist), searching 'BIU' finds no tier-1 (exact) hit, falls to
    tier-2 (anchored boundary), and resolves to 'BIU/GS - PM' alone — 'BIUK - PM'
    is correctly excluded because 'K' is alphanumeric and breaks the boundary.
    See BoundaryTests.test_biu_resolves_to_biu_gs_pm_when_biu_itself_is_absent.

  * If 'BIU' IS ALSO its own catalogue entry, searching 'BIU' hits tier-1
    (case-insensitive exact) FIRST, and tier 1 wins outright per the resolver's
    own contract — 'BIU/GS - PM' is never even considered, not merely excluded
    by the boundary rule. This is STRICTER than "resolves to BIU or BIU/GS - PM",
    not a violation of it: a code field that resolves to fewer things than an
    over-granting reading would allow is exactly the safe direction the spec
    says to keep ("a stricter code field is recoverable, an over-granting one is
    not"). See BoundaryTests.test_biu_resolves_only_to_itself_when_biu_also_exists.

  * 'BIU' NEVER matches 'BIUK - PM' in either scenario — the hard, non-negotiable
    half of the constraint — because 'K' immediately following 'BIU' is
    alphanumeric and the anchored regex refuses the match regardless of which
    tier is reached. Both scenarios below assert this explicitly.

Both scenarios are pinned so a future change to either the catalogue shape or
the resolver's tier order is caught here rather than discovered as a support
ticket.
"""
from django.test import TestCase

from events.models import Event
from paper_review.event_codes import (
    canonical_matches, normalise_event_code, resolve_paper_event_code,
)
from paper_review.models import PaperReview
from paper_review.tests import _Base, make_event
from teams.models import Team

# The five spacing/case variants A1 requires, each expected to resolve to the
# one canonical spelling stored in the catalogue: "AFS - JS".
SPACING_VARIANTS = [
    "AFS - JS",
    "AFS-JS",
    "afs-js",
    "AFS  -  JS",
    "  AFS - JS",
]


class NormalisationTests(TestCase):
    """The pure function, no DB involved."""

    def test_all_five_variants_normalise_identically(self):
        keys = {normalise_event_code(v) for v in SPACING_VARIANTS}
        self.assertEqual(keys, {"AFS-JS"})

    def test_separators_are_kept_not_collapsed(self):
        """
        Dropping '-' entirely would make "BIU-K" and "BIUK" collide — exactly the
        over-match class this codebase has already been burned by.
        """
        self.assertNotEqual(normalise_event_code("BIU-K"),
                            normalise_event_code("BIUK"))

    def test_slash_variants_normalise_identically_too(self):
        self.assertEqual(normalise_event_code("BIU / GS - PM"),
                         normalise_event_code("BIU/GS-PM"))


class SpacingResolutionTests(TestCase):
    """
    Each of the five variants resolves, and every one stores the SAME canonical
    spelling — the catalogue's, not whatever the caller happened to type.

    Checked via `.matches`, NOT `.ok`/`.event`: a fresh Event defaults
    accepting_web_bookings=False (Event.save() derives it from web_bookings,
    which make_event() never sets), which would make the raw resolver's `.ok`
    read BOOKINGS_OFF and fail every one of these assertions for a reason that has
    nothing to do with spacing. This is exactly why
    paper_review/serializers.py:validate_event_code reads `resolution.matches`
    directly rather than `.ok` — "BOOKINGS_OFF counts as SUCCESS: paper reviews
    arrive for events that are not selling tickets online" (event_codes.py's own
    docstring) — and this test follows the same real consumption pattern rather
    than the raw resolver property.
    """

    @classmethod
    def setUpTestData(cls):
        cls.event = make_event("AFS - JS", "Aviation Fuel Summit 2026")

    def test_each_variant_resolves_and_stores_the_canonical_spelling(self):
        for variant in SPACING_VARIANTS:
            with self.subTest(variant=variant):
                resolution = resolve_paper_event_code(variant)
                self.assertEqual(resolution.matched_codes, ["AFS - JS"],
                                 resolution.diagnostic)

    def test_canonical_matches_finds_exactly_one_stored_code(self):
        for variant in SPACING_VARIANTS:
            with self.subTest(variant=variant):
                self.assertEqual(canonical_matches(variant), ["AFS - JS"])

    def test_end_to_end_through_the_serializer_stores_the_canonical_spelling(self):
        """
        The unit-level resolver result is one thing; what actually lands in the
        column via validate_event_code is the claim that matters. Run through a
        full-visibility user so only event-code resolution is under test, not
        RBAC scope (that is A2's job).
        """
        from rest_framework.test import APIRequestFactory

        from django.contrib.auth import get_user_model

        from paper_review.serializers import PaperReviewSerializer

        U = get_user_model()
        admin = U.objects.create_user(
            username="ec_admin", password="x", email="ec_admin@example.com",
            role="admin", team=Team.objects.create(
                name="EC Admin", is_all_access=True),
        )
        request = APIRequestFactory().post("/api/paper-reviews/")
        request.user = admin

        full_payload = {
            "paper_submission_date": "2026-08-01", "speaker_name": "X",
            "company_name": "Co", "linkedin_speaker": "https://linkedin.com/in/x",
            "linkedin_followers": 10,
            "closeness_to_topic": 1, "closeness_to_region": 1,
            "clear_solution_to_challenges": 1, "case_study_results_examples": 1,
            "not_obvious_sales_pitch": 1, "company_profile_score": 1,
            "session_location_on_agenda": "Day 1", "proposal_received": "p",
            "theme": "t", "agenda_addition": "a",
        }
        for variant in SPACING_VARIANTS:
            with self.subTest(variant=variant):
                data = {**full_payload, "event_code": variant,
                       "email": f"{abs(hash(variant))}@example.com"}
                s = PaperReviewSerializer(data=data, context={"request": request})
                self.assertTrue(s.is_valid(), s.errors)
                self.assertEqual(s.validated_data["event_code"], "AFS - JS")


class BoundaryTests(TestCase):
    """
    The hard, non-negotiable constraint, traced under BOTH catalogue shapes so the
    actual (protected, unmodified) resolver behaviour is pinned rather than
    assumed. webhooks/event_resolver.py is not touched by this pass.
    """

    def test_biu_resolves_to_biu_gs_pm_when_biu_itself_is_absent(self):
        make_event("BIU/GS - PM")
        make_event("BIUK - PM")

        resolution = resolve_paper_event_code("BIU")
        self.assertEqual(resolution.matched_codes, ["BIU/GS - PM"],
                         resolution.diagnostic)

    def test_biu_resolves_only_to_itself_when_biu_also_exists(self):
        """
        THE TRACED, ACTUAL BEHAVIOUR: tier 1 (exact) wins outright once a literal
        'BIU' event exists, so 'BIU/GS - PM' is never reached — not because the
        boundary rule excluded it, but because the tier above it already answered.
        Stricter than "also BIU/GS - PM", which the spec explicitly allows.
        """
        make_event("BIU")
        make_event("BIU/GS - PM")
        make_event("BIUK - PM")

        resolution = resolve_paper_event_code("BIU")
        self.assertEqual(resolution.matched_codes, ["BIU"], resolution.diagnostic)
        self.assertNotIn("BIU/GS - PM", resolution.matched_codes)
        self.assertNotIn("BIUK - PM", resolution.matched_codes)

    def test_biu_never_resolves_to_biuk_in_either_catalogue_shape(self):
        for extra in (lambda: None, lambda: make_event("BIU")):
            with self.subTest(literal_biu_exists=extra is not None):
                Event.objects.all().delete()
                extra()
                make_event("BIU/GS - PM")
                make_event("BIUK - PM")

                resolution = resolve_paper_event_code("BIU")
                self.assertTrue(resolution.matches, resolution.diagnostic)
                self.assertNotIn("BIUK - PM", resolution.matched_codes)

    def test_biuk_resolves_to_biuk_only(self):
        make_event("BIU/GS - PM")
        make_event("BIUK - PM")

        resolution = resolve_paper_event_code("BIUK")
        self.assertEqual(resolution.matched_codes, ["BIUK - PM"],
                         resolution.diagnostic)

    def test_biuk_as_a_literal_catalogue_entry_resolves_to_itself(self):
        make_event("BIUK")
        make_event("BIUK - PM")
        resolution = resolve_paper_event_code("BIUK")
        self.assertEqual(resolution.matched_codes, ["BIUK"])


class UnknownCodeTests(_Base):
    """
    A genuinely unknown code names the catalogue field, since the candidate list
    comes back empty — traced through the actual 400 response, not just the
    resolver's own message.
    """

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_unresolvable_code_names_the_catalogue_field(self):
        resolution = resolve_paper_event_code("TOTALLY-UNKNOWN-CODE")
        self.assertFalse(resolution.ok)
        self.assertEqual(resolution.candidates, [])

        r = self.client.post(self.LIST,
                             self.payload(event_code="TOTALLY-UNKNOWN-CODE"),
                             format="json")
        self.assertEqual(r.status_code, 400, r.content)
        message = str(r.data["event_code"])
        self.assertIn("event catalogue", message.lower())
        # Names the catalogue FIELD specifically, since candidates came back
        # empty and there is nothing else useful left to tell the caller.
        self.assertIn("Event.event_code", message)

    def test_a_spacing_collision_in_the_catalogue_is_reported_as_ambiguous(self):
        """
        Two catalogue entries differing only in spacing is a genuine ambiguity —
        event_codes.py refuses to guess between them rather than silently
        picking one.
        """
        make_event("DUP-CODE")
        make_event("DUP - CODE")
        r = self.client.post(self.LIST, self.payload(event_code="DUP-CODE"),
                             format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("differ only in spacing", str(r.data["event_code"]))
