"""
accounts/tests_import_hardening.py
───────────────────────────────────
The Phase 2 defect fixes, one class each.

  2.1 edition accepts nonsense        → EditionRangeTests
  2.2 sales exec by name substring    → UserResolutionTests
  2.4 booking_code by substring       → BookingCodeTests

Each class opens with the OLD behaviour written as an assertion, so the test
records what was wrong rather than only what is now right.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.import_common import EDITION_MAX, EDITION_MIN, parse_edition
from accounts.user_resolution import AMBIGUOUS, NO_MATCH, UserResolver
from book_event.booking_code import (
    DELEGATE, SPEAKER_SALES, SPEX, category_q, classify, speaker_q, spex_q,
)
from book_event.booking_code_canonical import DEFAULT_BOOKING_CODE

User = get_user_model()


# ══ 2.1 EDITION RANGE ═══════════════════════════════════════════════════════

class EditionRangeTests(TestCase):
    """
    `edition` is IntegerField, so `int("45678")` raised nothing and stored a
    45,678th edition forever. Excel serials in a date-formatted column are
    exactly how such a value arrives.
    """

    def test_an_excel_serial_is_now_rejected(self):
        value, error = parse_edition(45678)
        self.assertIsNone(value)
        self.assertIn("45678", error)
        self.assertIn("Excel serial", error)

    def test_the_old_code_would_have_accepted_it(self):
        """Records the defect: a bare int() is perfectly happy with 45678."""
        self.assertEqual(int("45678"), 45678)

    def test_a_real_year_passes(self):
        for year in (2000, 2024, 2026, 2100):
            value, error = parse_edition(year)
            self.assertIsNone(error, f"{year} should be valid")
            self.assertEqual(value, year)

    def test_a_two_digit_year_expands(self):
        self.assertEqual(parse_edition(26)[0], 2026)
        self.assertEqual(parse_edition("26")[0], 2026)

    def test_the_boundaries_are_inclusive(self):
        self.assertEqual(parse_edition(EDITION_MIN)[0], EDITION_MIN)
        self.assertEqual(parse_edition(EDITION_MAX)[0], EDITION_MAX)

    def test_just_outside_the_boundary_is_rejected(self):
        self.assertIsNone(parse_edition(EDITION_MAX + 1)[0])
        self.assertIsNone(parse_edition(1999)[0])

    def test_blank_is_not_an_error(self):
        for blank in (None, "", "   "):
            value, error = parse_edition(blank)
            self.assertIsNone(value)
            self.assertIsNone(error)

    def test_non_numeric_is_an_error_quoting_the_value(self):
        value, error = parse_edition("not a year")
        self.assertIsNone(value)
        self.assertIn("not a year", error)


# ══ 2.2 USER RESOLUTION ═════════════════════════════════════════════════════

class UserResolutionTests(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user(
            username="ada.lovelace", password="x", role=User.Role.SALES,
            email="ada@example.com", first_name="Ada", last_name="Lovelace")
        self.anastasia = User.objects.create_user(
            username="anastasia.k", password="x", role=User.Role.SALES,
            email="anastasia@example.com", first_name="Anastasia", last_name="Kirov")
        self.resolver = UserResolver()

    def test_email_resolves(self):
        user, reason = self.resolver.resolve("ADA@example.com")
        self.assertEqual(user, self.ada)
        self.assertIsNone(reason)

    def test_username_resolves(self):
        self.assertEqual(self.resolver.resolve("ada.lovelace")[0], self.ada)

    def test_full_name_resolves_exactly(self):
        self.assertEqual(self.resolver.resolve("Ada Lovelace")[0], self.ada)

    def test_a_spaced_name_matching_a_dotted_username_resolves(self):
        """
        "Anastasia K" -> "anastasia.k" is an EXACT username hit, not a substring
        guess, so it resolves. This tier is kept from the old chain deliberately.
        """
        self.assertEqual(self.resolver.resolve("Anastasia K")[0], self.anastasia)
        self.assertEqual(self.resolver.resolve("anastasia.k")[0], self.anastasia)

    def test_a_substring_of_a_real_name_does_NOT_resolve(self):
        """
        The defect. `first_name__icontains="Ana"` matched Anastasia and `.first()`
        picked her, attributing the booking to the wrong person with no signal.
        """
        user, reason = self.resolver.resolve("Ana")
        self.assertIsNone(user)
        self.assertEqual(reason, NO_MATCH)

    def test_the_old_query_would_have_matched_the_wrong_person(self):
        """Records the defect against the real ORM, not a description of it."""
        hit = User.objects.filter(first_name__icontains="Ana").first()
        self.assertIsNotNone(hit)
        self.assertEqual(hit.first_name, "Anastasia")

    def test_two_users_with_the_same_name_are_ambiguous_not_arbitrary(self):
        User.objects.create_user(
            username="ada.two", password="x", role=User.Role.SALES,
            email="ada2@example.com", first_name="Ada", last_name="Lovelace")
        resolver = UserResolver()
        user, reason = resolver.resolve("Ada Lovelace")
        self.assertIsNone(user)
        self.assertEqual(reason, AMBIGUOUS)

    def test_the_resolution_rate_is_reported(self):
        for value in ("ada@example.com", "ada.lovelace", "Nobody At All"):
            self.resolver.resolve(value)
        report = self.resolver.report()
        self.assertEqual(report["attempted"], 3)
        self.assertEqual(report["resolved"], 2)
        self.assertAlmostEqual(report["resolution_rate"], 2 / 3)
        self.assertEqual(report["unresolved_values"][0]["value"], "Nobody At All")

    def test_empty_values_are_excluded_from_the_rate(self):
        """A booking naming nobody is not a resolution failure."""
        self.resolver.resolve("")
        self.resolver.resolve(None)
        self.resolver.resolve("ada@example.com")
        self.assertEqual(self.resolver.report()["attempted"], 1)
        self.assertEqual(self.resolver.resolution_rate, 1.0)

    def test_the_rate_is_none_when_nothing_was_attempted(self):
        self.assertIsNone(UserResolver().resolution_rate)

    def test_a_repeated_bad_value_is_reported_once_with_its_weight(self):
        for _ in range(400):
            self.resolver.resolve("Ghost Person")
        report = self.resolver.report()
        self.assertEqual(report["unresolved_distinct"], 1)
        self.assertEqual(report["unresolved_rows"], 400)


# ══ 2.4 BOOKING CODE CLASSIFICATION ═════════════════════════════════════════

class BookingCodeTests(TestCase):
    def test_spex_marker_matches_when_anchored(self):
        for code in ("SpEx", "SLV SpEx", "Speaker / SLV SpEx", "spex-2026"):
            self.assertEqual(classify(code), SPEX, code)

    def test_add_ons_matches_as_a_whole_string(self):
        self.assertEqual(classify("Add-Ons"), SPEX)
        self.assertEqual(classify("add-ons"), SPEX)

    def test_speaker_markers_match_when_anchored(self):
        for code in ("Speaker", "SPP", "Speaker Pass", "VIP/SPP"):
            self.assertEqual(classify(code), SPEAKER_SALES, code)

    # Codes that genuinely CONTAIN "spp" without it being a standalone marker.
    # These are the ones the old icontains rule silently counted as speaker sales.
    SPP_SUBSTRING_CODES = ("SPPX", "GSPP", "SPPX-2026", "CRISPP")

    def test_spp_inside_a_longer_token_does_NOT_match(self):
        """
        The defect. `icontains="spp"` counted every one of these as speaker
        sales, and nothing surfaced it — the number was just wrong.
        """
        for code in self.SPP_SUBSTRING_CODES:
            self.assertEqual(classify(code), DELEGATE, code)

    def test_the_old_substring_rule_would_have_caught_them(self):
        """Records the defect: these really do satisfy icontains="spp"."""
        for code in self.SPP_SUBSTRING_CODES:
            self.assertIn("spp", code.lower())

    def test_unrelated_codes_are_delegate(self):
        for code in ("SUPPLEMENT", "Supplier Pass", "Delegate Pass"):
            self.assertEqual(classify(code), DELEGATE, code)

    def test_an_empty_or_missing_code_is_delegate(self):
        for code in ("", None, "   "):
            self.assertEqual(classify(code), DELEGATE)

    def test_spex_wins_a_collision_in_the_exclusive_api(self):
        self.assertEqual(classify("Speaker / SLV SpEx"), SPEX)

    @override_settings(BOOKING_CODE_SPEAKER_MARKERS=["keynote"])
    def test_the_marker_lists_are_config_driven(self):
        """
        The lists must be correctable from real export data without touching
        query code — the defaults are carried forward, not verified.
        """
        self.assertEqual(classify("Keynote Pass"), SPEAKER_SALES)
        self.assertEqual(classify("SPP"), DELEGATE)


class BookingCodeQueryTests(TestCase):
    """
    The Q objects must apply the SAME rule as classify(). Two implementations of
    one rule is how this class of bug survives a fix, so they are checked against
    a shared corpus rather than trusted to agree.
    """

    CORPUS = [
        "SpEx", "SLV SpEx", "Add-Ons", "Speaker", "SPP", "Speaker Pass",
        "SUPPLEMENT", "SPPX", "Supplier Pass", "Delegate Pass", "",
        "Speaker / SLV SpEx",
    ]

    def setUp(self):
        from datetime import date

        from book_event.models import BookEvent
        from events.models import Event

        Event.objects.create(event_code="EV - XX", official_event_name="EV",
                             event_date=date(2026, 1, 1))
        for i, code in enumerate(self.CORPUS):
            BookEvent.objects.create(invoice_number=f"INV-{i}",
                                     event_code="EV - XX", booking_code=code)

    def _codes(self, q):
        from book_event.models import BookEvent
        return set(BookEvent.objects.filter(q).values_list("booking_code", flat=True))

    def _stored(self):
        """
        What the table actually HOLDS, which is not the corpus verbatim.

        BookEvent.save() defaults a blank booking_code to
        booking_code_canonical.DEFAULT_BOOKING_CODE, "Delegate", so the ""
        row above is stored as "Delegate". Both tests below used to build their
        Python answer from the raw corpus and their SQL answer from the table,
        so they disagreed by exactly that row — reported as "'Delegate' in the
        first set, '' in the second", which reads like a rule disagreement and
        is not one: classify("") and classify("Delegate") both say delegate.

        Comparing over the stored values keeps the property this class exists
        for, the Q objects and classify() agreeing on every row the query can
        ever see, and stops asserting that the model stores the string it was
        given, which is another module's business and deliberately no longer
        true.
        """
        from book_event.models import BookEvent
        return set(BookEvent.objects.values_list("booking_code", flat=True))

    def test_the_exclusive_q_agrees_with_classify_on_every_corpus_row(self):
        stored = self._stored()
        for category in (SPEX, SPEAKER_SALES, DELEGATE):
            from_db = self._codes(category_q(category))
            from_py = {c for c in stored if classify(c) == category}
            self.assertEqual(from_db, from_py, f"disagreement on {category}")

    def test_the_exclusive_categories_partition_the_corpus(self):
        seen = set()
        for category in (SPEX, SPEAKER_SALES, DELEGATE):
            rows = self._codes(category_q(category))
            self.assertFalse(seen & rows, "categories must not overlap")
            seen |= rows
        self.assertEqual(seen, self._stored())

    def test_a_blank_booking_code_is_still_a_delegate(self):
        """
        The row the two tests above used to disagree over. It reaches the table
        as "Delegate" rather than "", and it must land in the delegate bucket
        either way — a booking with no code is a delegate booking.
        """
        self.assertEqual(classify(""), DELEGATE)
        self.assertIn(DEFAULT_BOOKING_CODE, self._codes(category_q(DELEGATE)))

    def test_the_overlapping_q_still_counts_a_hybrid_on_both(self):
        """views.py:181 documents this as intentional; only the rule changed."""
        self.assertIn("Speaker / SLV SpEx", self._codes(spex_q()))
        self.assertIn("Speaker / SLV SpEx", self._codes(speaker_q()))

    def test_the_overlapping_q_no_longer_catches_supplement(self):
        self.assertNotIn("SUPPLEMENT", self._codes(speaker_q()))
