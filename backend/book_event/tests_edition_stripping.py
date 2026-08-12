"""
book_event/tests_edition_stripping.py
──────────────────────────────────────
Characterises BookEvent.save()'s event-code / edition handling (models.py:154-174)
ahead of the Zoho load.

WHAT save() DOES
    match = re.search(r'(\\d{2,4})$', self.event_code)      # trailing 2-4 digits
    self.edition    = int("20" + num) if len(num) == 2 else int(num)
    self.event_code = re.sub(r'\\s*-?\\s*\\d{2,4}$', '', self.event_code).strip()
    master = Event.objects.filter(event_code=self.event_code).first()   # EXACT

THE CATALOGUE IT LOOKS UP AGAINST
Every one of the 142 rows in `events` is of the form "XXX - YY" — verified against
the live database: 0 codes end in 2-4 digits. So the master lookup is an EXACT
match against codes that never carry an edition, while the booking's own code is
whatever Zoho exported.

These tests do not assert a desired design; they pin the CURRENT behaviour so the
Phase 3 decision is made against measured facts rather than recollection, and so
any later fix has a baseline that fails loudly.

NOT VERIFIED HERE: what the real export's event_code column actually contains.
The export file is absent (Phase 0), so the inputs below are the plausible shapes
— catalogue-style, catalogue-plus-year, and compact-with-year — not observed ones.
"""
from django.test import TestCase

from book_event.models import BookEvent
from events.models import Event


def make_event(code, name):
    """
    Two non-obvious constraints on this fixture, both found the hard way:

    * Event.event_date is NOT NULL.
    * Event.save() (events/models.py:79-86) DERIVES `name` — it assigns
      official_event_name when that is set and falls back to event_code
      otherwise. Passing name= directly is silently discarded, so the catalogue
      name has to be supplied as official_event_name or every lookup below reads
      back the event code as the event's name.
    """
    return Event.objects.create(event_code=code, official_event_name=name,
                                event_date="2026-01-01")


def make_booking(code, **over):
    kwargs = {
        "invoice_number": over.pop("invoice_number", f"INV-{abs(hash(code)) % 10**6}"),
        "event_code": code,
    }
    kwargs.update(over)
    obj = BookEvent(**kwargs)
    obj.save()
    obj.refresh_from_db()
    return obj


class CatalogueShapeTests(TestCase):
    """The catalogue format the lookup has to hit."""

    def test_a_catalogue_code_survives_untouched(self):
        """
        "BIUK - PM" has no trailing 2-4 digit run, so nothing is stripped and the
        exact lookup succeeds. This is the case that already works.
        """
        make_event("BIUK - PM", "BI UK 2026")
        b = make_booking("BIUK - PM")
        self.assertEqual(b.event_code, "BIUK - PM")
        self.assertIsNone(b.edition)
        self.assertEqual(b.event_name, "BI UK")   # trailing year stripped from name

    def test_a_catalogue_code_with_a_year_loses_the_year_and_still_matches(self):
        """
        "BIUK - PM 26" → edition 2026, code back to "BIUK - PM", lookup succeeds.
        This is the case the stripping logic was written for and it works.
        """
        make_event("BIUK - PM", "BI UK 2026")
        b = make_booking("BIUK - PM 26")
        self.assertEqual(b.event_code, "BIUK - PM")
        self.assertEqual(b.edition, 2026)
        self.assertEqual(b.event_name, "BI UK 2026")


class CompactCodeTests(TestCase):
    """
    The failure shape. A compact Zoho code like "BIUK26" strips to "BIUK", which
    is NOT how the catalogue spells it ("BIUK - PM"), so the master lookup misses
    and event_name is never populated.
    """

    def test_a_compact_code_strips_to_something_the_catalogue_does_not_contain(self):
        make_event("BIUK - PM", "BI UK 2026")
        b = make_booking("BIUK26")
        self.assertEqual(b.edition, 2026)
        self.assertEqual(b.event_code, "BIUK")
        self.assertNotEqual(b.event_code, "BIUK - PM")
        self.assertEqual(
            b.event_name, "",
            "master lookup missed, so event_name stayed empty — this is the "
            "'86 of 215 unmatched' shape",
        )

    def test_a_four_digit_year_is_also_stripped(self):
        make_event("CCU - VV", "CCU 2026")
        b = make_booking("CCU2026")
        self.assertEqual(b.edition, 2026)
        self.assertEqual(b.event_code, "CCU")
        self.assertEqual(b.event_name, "")


class EditionColumnTests(TestCase):
    """
    `edition` is IntegerField(null=True) — models.py:63. Nothing bounds it, so an
    Excel serial arriving in the import's `edition` column is stored verbatim as
    an integer. It cannot raise a type error the way a text column would; it just
    becomes a silently wrong edition.

    The import reads it at book_event/views.py:713 as
        edition_val = int(row.get("edition")) if row.get("edition") else None
    with no range check.
    """

    def test_an_excel_serial_is_accepted_as_an_edition_verbatim(self):
        b = make_booking("STANDALONE", edition=45678)
        self.assertEqual(
            b.edition, 45678,
            "no bound on edition: an Excel serial lands as a 45678th edition",
        )

    def test_a_code_suffix_overwrites_a_supplied_edition(self):
        """
        Precedence worth knowing before the load: when the code carries a year,
        save() OVERWRITES whatever the edition column said.
        """
        b = make_booking("BIUK - PM 26", edition=45678)
        self.assertEqual(b.edition, 2026)

    def test_without_a_code_suffix_the_supplied_edition_survives(self):
        """The complementary case — this is how a serial actually reaches the DB."""
        b = make_booking("BIUK - PM", edition=45678)
        self.assertEqual(b.edition, 45678)
