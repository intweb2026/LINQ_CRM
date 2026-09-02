"""
paper_review/tests_session_options.py
──────────────────────────────────────
The agenda slots the form offers must cover every slot the data holds.

THE FAILURE THIS PINS
frontend/src/lib/constants.js PAPER_SESSION_OPTIONS listed four slots while
paper_reviews held ten, so six values in active use — 941 rows — could not be
selected. The picker in PaperReviewFields.jsx renders no option for a stored value
it does not recognise, and session_location_on_agenda is REQUIRED, so opening one
of those rows showed "— Select —" and saving it forced the reviewer onto a
different slot. A missing option is not a cosmetic gap here; it rewrites data.

WHY THIS IS ASSERTED AGAINST THE JS
The list has no server-side counterpart to check it against. There is no choices=
on the model and deliberately so — see PaperReviewViewSet.OPTION_FIELDS, whose
comment explains that the filter dropdowns serve what the data actually contains
precisely because the vocabulary was never confirmed. That leaves the JS array as
the only declaration of it, and a hand-edited frontend file is exactly what drifts.
Same approach as accounts/tests_event_picker_sources.py and
accounts/tests_pipeline_modules.py, and skipped the same way on a backend-only
checkout.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from .models import PaperReview

CONSTANTS = (Path(settings.BASE_DIR).parent
             / "frontend" / "src" / "lib" / "constants.js")

# Confirmed against the ten distinct values in the live database, 2026-09-02.
# Chronological, which is the order the form renders: "Afternoon Opening Session"
# runs BEFORE "Afternoon Session" and sorts after it, so alphabetical would read
# wrong on the page.
EXPECTED = [
    "Day 1, Opening Session",
    "Day 1, Morning Session",
    "Day 1, Afternoon Opening Session",
    "Day 1, Afternoon Session",
    "Day 1, Closing Session",
    "Day 2, Opening Session",
    "Day 2, Morning Session",
    "Day 2, Afternoon Opening Session",
    "Day 2, Afternoon Session",
    "Day 2, Closing Session",
]


def frontend_options():
    """The values PAPER_SESSION_OPTIONS lists, in declaration order."""
    source = CONSTANTS.read_text(encoding="utf-8")
    match = re.search(
        r"PAPER_SESSION_OPTIONS\s*=\s*\[(.*?)\]", source, re.S,
    )
    assert match, "PAPER_SESSION_OPTIONS not found in constants.js"
    return re.findall(r"'([^']+)'", match.group(1))


class SessionOptionsTests(TestCase):
    def setUp(self):
        if not CONSTANTS.exists():
            self.skipTest("frontend/src not present in this checkout")

    def test_the_form_offers_every_slot_in_the_agreed_vocabulary(self):
        self.assertEqual(frontend_options(), EXPECTED)

    def test_the_field_is_wide_enough_for_the_longest_of_them(self):
        # max_length=100 against a 32-character value today. Asserted because a
        # slot added later is exactly the kind of value that gets silently
        # truncated on import rather than refused.
        limit = PaperReview._meta.get_field("session_location_on_agenda").max_length
        self.assertLessEqual(max(len(v) for v in EXPECTED), limit)

    def test_no_slot_is_listed_twice_or_carries_stray_spacing(self):
        # A duplicate renders two identical options; a stray space makes a value
        # that looks right and matches nothing stored.
        options = frontend_options()
        self.assertEqual(len(options), len(set(options)))
        for value in options:
            self.assertEqual(value, value.strip())
            self.assertNotIn("  ", value)
