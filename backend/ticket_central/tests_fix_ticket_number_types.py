"""One check on the prefix rewrite: what it changes, and what it must not touch."""
from django.test import SimpleTestCase

from ticket_central.management.commands.fix_ticket_number_types import retype


class RetypeTests(SimpleTestCase):
    def test_priority_prefix_becomes_the_type_code(self):
        self.assertEqual(
            retype("SPEX-PPTX 10037", "PPTX", "Blue - BX"), "BX-PPTX 10037",
        )

    def test_untyped_number_gains_its_type(self):
        self.assertEqual(retype("CEU 10001", "CEU", "Comp.-CX"), "CX-CEU 10001")

    def test_legacy_letter_suffix_is_carried_over(self):
        self.assertEqual(retype("SPEX-HIU 11A", "HIU", "ZID"), "ZID-HIU 11A")

    def test_correct_number_is_left_alone(self):
        self.assertIsNone(retype("BX-CEU 10001", "CEU", "Blue - BX"))

    def test_junk_and_missing_data_are_left_alone(self):
        self.assertIsNone(retype("Delete", "CEU", "Blue - BX"))     # no tail
        self.assertIsNone(retype("CEU 10001", "CEU", "Mauve"))      # unknown type
        self.assertIsNone(retype("CEU 10001", "", "Blue - BX"))     # no purpose
