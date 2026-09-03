"""
Regression: a purpose whose imported rows hold two disjoint Zoho ranges must
not have new tickets numbered into the low range.

Real case from production: FLE carried GR-FLE 5 and YL-FLE 21-24 alongside
7041-7221. The old gap-scan started at min(used)=5 and issued 6, then 7.
"""
from django.test import TestCase
from .models import Ticket, TicketSequence
from .utils import assign_next_ticket_number


class HighWaterNumberingTests(TestCase):
    def test_low_outliers_do_not_pull_new_numbers_down(self):
        for tn in ("GR-FLE 5", "YL-FLE 21", "YL-FLE 24", "WH-FLE 7221"):
            Ticket.objects.create(purpose="FLE", ticket_number=tn)
        TicketSequence.objects.create(purpose_key="FLE", last_number=24)

        self.assertEqual(assign_next_ticket_number("FLE"), "FLE 7222")

    def test_deleted_number_is_not_reissued(self):
        Ticket.objects.create(purpose="PSZ", ticket_number="CX-PSZ 7044")
        TicketSequence.objects.create(purpose_key="PSZ", last_number=7044)

        first = assign_next_ticket_number("PSZ")
        Ticket.objects.create(purpose="PSZ", ticket_number=first)
        Ticket.objects.filter(ticket_number=first).delete()

        self.assertEqual(first, "PSZ 7045")
        self.assertEqual(assign_next_ticket_number("PSZ"), "PSZ 7046")

    def test_data_outranks_the_10000_default(self):
        """
        FLE tops out at 7221 and has no counter row. The default 10000 must not
        out-rank that and strand the live series at 10001.
        """
        Ticket.objects.create(purpose="FLE", ticket_number="YL-FLE 7221")
        self.assertFalse(TicketSequence.objects.filter(purpose_key="FLE").exists())

        self.assertEqual(assign_next_ticket_number("FLE"), "FLE 7222")

    def test_an_existing_counter_still_outranks_thinner_data(self):
        """
        A pre-existing row is real history; it remembers numbers whose tickets
        were deleted, so it must not be dragged back down to the data's max.
        """
        Ticket.objects.create(purpose="PSZ", ticket_number="CX-PSZ 7044")
        TicketSequence.objects.create(purpose_key="PSZ", last_number=7050)

        self.assertEqual(assign_next_ticket_number("PSZ"), "PSZ 7051")

    def test_fresh_purpose_starts_at_10001(self):
        self.assertEqual(assign_next_ticket_number("BRANDNEW"), "BRANDNEW 10001")


class PurposeKeyNormalisationTests(TestCase):
    """`purpose` is free text and keys the counter, so variants used to split it."""

    def test_case_variants_share_one_sequence(self):
        Ticket.objects.create(purpose="CCU", ticket_number="BX-CCU 10001")
        TicketSequence.objects.create(purpose_key="CCU", last_number=10001)

        # Typed lowercase in the form; must not open a second counter at 10001.
        self.assertEqual(assign_next_ticket_number("ccu"), "CCU 10002")
        self.assertEqual(TicketSequence.objects.filter(
            purpose_key__iexact="CCU").count(), 1)

    def test_long_purpose_does_not_overflow_the_key(self):
        """purpose is 255 chars, purpose_key is 50 — this used to raise DataError."""
        number = assign_next_ticket_number("X" * 200)

        self.assertTrue(number.endswith(" 10001"))
        self.assertEqual(TicketSequence.objects.get().purpose_key, "X" * 50)

    def test_distinct_purposes_stay_distinct(self):
        """"ODU b" is not assumed to be a typo for "ODU"."""
        from .utils import extract_purpose_code
        self.assertNotEqual(extract_purpose_code("ODU b"),
                            extract_purpose_code("ODU"))


class BackfillUsesSharedGeneratorTests(TestCase):
    """The cron used to do its own unlocked `last_number + 1` from a snapshot."""

    def test_backfill_continues_the_live_series(self):
        from django.core.management import call_command
        Ticket.objects.create(purpose="FLE", ticket_number="GR-FLE 5")
        Ticket.objects.create(purpose="FLE", ticket_number="WH-FLE 7221")
        TicketSequence.objects.create(purpose_key="FLE", last_number=24)
        blank = Ticket.objects.create(purpose="FLE", type_of_ticket="WH",
                                      ticket_number="")

        call_command("backfill_ticket_numbers")

        blank.refresh_from_db()
        self.assertEqual(blank.ticket_number, "FLE 7222")

    def test_dry_run_writes_nothing(self):
        from django.core.management import call_command
        blank = Ticket.objects.create(purpose="FLE", type_of_ticket="WH",
                                      ticket_number="")

        call_command("backfill_ticket_numbers", "--dry-run")

        blank.refresh_from_db()
        self.assertEqual(blank.ticket_number, "")
        self.assertFalse(TicketSequence.objects.exists())


class PurposeIsStoredUpperCaseTests(TestCase):
    """`purpose` is stored upper-case, not just rendered that way."""

    def test_model_save_uppercases(self):
        t = Ticket.objects.create(purpose="ccu", type_of_ticket="BX")
        t.refresh_from_db()
        self.assertEqual(t.purpose, "CCU")

    def test_editing_an_existing_ticket_uppercases(self):
        t = Ticket.objects.create(purpose="CCU", type_of_ticket="BX")
        t.purpose = "pharma general"
        t.save()
        t.refresh_from_db()
        self.assertEqual(t.purpose, "PHARMA GENERAL")

    def test_internal_whitespace_is_collapsed(self):
        t = Ticket.objects.create(purpose="  pharma   general  ")
        t.refresh_from_db()
        self.assertEqual(t.purpose, "PHARMA GENERAL")

    def test_blank_purpose_survives(self):
        t = Ticket.objects.create(purpose="", type_of_ticket="BX")
        t.refresh_from_db()
        self.assertEqual(t.purpose, "")

    def test_import_row_coercion_uppercases(self):
        """The import's update branch writes via queryset.update(), not save()."""
        from .utils import _coerce_row
        coerced = _coerce_row({"purpose": "ccu", "type_of_ticket": "BX"})
        self.assertEqual(coerced["purpose"], "CCU")
