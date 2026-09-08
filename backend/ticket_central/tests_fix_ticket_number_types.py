"""One check on the prefix rewrite: what it changes, and what it must not touch."""
from django.test import SimpleTestCase

from ticket_central.management.commands.fix_ticket_number_types import retype


class RetypeTests(SimpleTestCase):
    def test_purpose_only_number_gains_its_type(self):
        self.assertEqual(retype("BRE 10330", "BRE", "Blue - BX"), "BX-BRE 10330")
        self.assertEqual(retype("CEU 10001", "CEU", "Comp.-CX"), "CX-CEU 10001")

    def test_legacy_letter_suffix_is_carried_over(self):
        self.assertEqual(retype("HIU 11A", "HIU", "ZID"), "ZID-HIU 11A")

    def test_purpose_containing_a_dash_is_not_read_as_prefixed(self):
        self.assertEqual(retype("ODU-B 10004", "ODU-B", "Green - GR"),
                         "GR-ODU-B 10004")

    def test_an_existing_prefix_is_never_touched(self):
        # Already correct.
        self.assertIsNone(retype("BX-CEU 10001", "CEU", "Blue - BX"))
        # Priority sitting where the type would go. Out of scope on purpose.
        self.assertIsNone(retype("SPEX-PPTX 10037", "PPTX", "Blue - BX"))
        self.assertIsNone(retype("ASSOC-SCE 10194", "SCE", "Green - GR"))
        # A type code that no longer matches the row's type. Also out of scope.
        self.assertIsNone(retype("CX-LAE 10182", "LAE", "Yellow - YL"))

    def test_junk_and_missing_data_are_left_alone(self):
        self.assertIsNone(retype("Delete", "CEU", "Blue - BX"))     # no tail
        self.assertIsNone(retype("CEU 10001", "CEU", "Mauve"))      # unknown type
        self.assertIsNone(retype("CEU 10001", "", "Blue - BX"))     # no purpose
