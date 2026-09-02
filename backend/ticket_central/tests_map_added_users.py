"""
The parse half of map_added_users. No DB: the table is the thing that can rot.
"""
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from ticket_central.management.commands.map_added_users import (
    _OWNERSHIP, parse_ownership,
)


class ParseOwnershipTests(SimpleTestCase):

    def test_keys_are_stored_form(self):
        """
        Keys must match the column, which normalize_purpose() upper-cases and
        whitespace-collapses. The sheet has "TIEf", "ODU b", "Pharma Generic"
        and a trailing-space "SGE ", none of which would match a ticket as
        typed.
        """
        owners = parse_ownership()
        for key in owners:
            self.assertEqual(key, key.upper(), key)
            self.assertEqual(key, " ".join(key.split()), repr(key))
        self.assertEqual(owners["TIEF"], "Percy Tovar")
        self.assertEqual(owners["ODU B"], "Percy Tovar")
        self.assertEqual(owners["PHARMA GENERIC"], "Percy Tovar")
        # "SGE" appears twice in the sheet, once with a trailing space; both
        # rows say Paxton Medina, so the collapse must not raise.
        self.assertEqual(owners["SGE"], "Paxton Medina")

    def test_two_space_separator_parses(self):
        """A paste that lost its tabs still maps."""
        self.assertEqual(
            parse_ownership("CCU   Vick Varela\nADA   Percy Tovar"),
            {"CCU": "Vick Varela", "ADA": "Percy Tovar"},
        )

    def test_conflicting_owner_raises(self):
        """
        Two owners for one purpose is a data question, not something to resolve
        by whichever line is last.
        """
        with self.assertRaises(CommandError):
            parse_ownership("CCU\tVick Varela\nCCU\tRay Santos")

    def test_unparseable_line_raises(self):
        with self.assertRaises(CommandError):
            parse_ownership("CCU Vick Varela")   # single space: no separator

    def test_table_is_whole(self):
        owners = parse_ownership()
        self.assertEqual(len(owners), 419)
        self.assertEqual(len(set(owners.values())), 8)
        # Every owner is "First Last": resolve_owners partitions on the first
        # space, so a single-token or three-token name would resolve to nobody
        # and abort the run.
        for name in set(owners.values()):
            self.assertEqual(len(name.split()), 2, name)
        # A stray blank line in the middle of the table must not become a key.
        self.assertNotIn("", owners)
        self.assertEqual(len(_OWNERSHIP.strip().splitlines()), 420)
