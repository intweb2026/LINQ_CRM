"""
webhooks/tests_event_code_normalization.py
─────────────────────────────────────────────
C2 — the shared spacing-tolerant layer, tested at its actual home rather than
only through the two apps that consume it (paper_review/tests_event_codes.py and
proposal_submission/tests_extras.py:EventCodeNormalizationTests both exercise it
end-to-end through their own serializers/importer; this file is the unit-level
guard on the module itself).
"""
from datetime import date

from django.test import TestCase

from events.models import Event
from webhooks.event_code_normalization import (
    canonical_matches, normalise_event_code, resolve_with_spacing_tolerance,
)


def make_event(code):
    return Event.objects.create(
        event_code=code, official_event_name=code, event_date=date(2026, 5, 1))


class NormalisationTests(TestCase):
    def test_spacing_and_case_variants_share_one_key(self):
        variants = ["AFS - JS", "AFS-JS", "afs-js", "AFS  -  JS", "  AFS - JS"]
        self.assertEqual({normalise_event_code(v) for v in variants}, {"AFS-JS"})

    def test_separators_survive_normalisation(self):
        self.assertNotEqual(normalise_event_code("BIU-K"),
                            normalise_event_code("BIUK"))

    def test_blank_normalises_to_blank(self):
        self.assertEqual(normalise_event_code(""), "")
        self.assertEqual(normalise_event_code(None), "")


class ResolutionTests(TestCase):
    def test_a_spacing_variant_resolves_to_the_canonical_stored_spelling(self):
        make_event("AFS - JS")
        resolution = resolve_with_spacing_tolerance("AFS-JS")
        self.assertEqual(resolution.matched_codes, ["AFS - JS"])

    def test_two_spacing_equivalent_catalogue_entries_is_a_collision(self):
        make_event("AFS-JS")
        make_event("AFS - JS")
        self.assertEqual(canonical_matches("afs - js"), ["AFS - JS", "AFS-JS"])

    def test_biu_never_matches_biuk(self):
        make_event("BIU/GS - PM")
        make_event("BIUK - PM")
        resolution = resolve_with_spacing_tolerance("BIU")
        self.assertEqual(resolution.matched_codes, ["BIU/GS - PM"])

    def test_a_code_with_no_spacing_equivalent_falls_through_unchanged(self):
        """
        0 canonical matches must not short-circuit — the raw code still reaches
        the underlying anchored-boundary resolver exactly as before this module
        existed.
        """
        make_event("ZZZ - QQ")
        resolution = resolve_with_spacing_tolerance("totally-unrelated-code")
        self.assertEqual(resolution.matches, [])
        self.assertEqual(resolution.candidates, [])
