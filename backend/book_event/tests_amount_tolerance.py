"""
book_event/tests_amount_tolerance.py
─────────────────────────────────────
Amount fields must never fail a booking payload.

This CRM does not track amounts; they are recorded elsewhere. Before this,
"$975.55" in TotalAmount raised a ValidationError, WebhookProcessor treated
that as a hard 400, and the delivery was discarded whole, invoice, company and
every delegate on it, over a field nothing here reads.

Serializer-only, so SimpleTestCase; no database is touched.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from book_event.serializers import WebsiteBookingSerializer


BASE = {
    "InvoiceNumber": "SFIL27CHI-6828",
    "Eventcode": "SFIL",
    "Delegates": [
        {"FirstName": "Jeannie", "LastName": "Shaughnessy", "Email": "jeannie@ptnpa.org"},
    ],
}

AMOUNT_FIELDS = ("PreTaxAmount", "TaxAmount", "TotalAmount", "AddOnsTotalAmount")


def _run(**overrides):
    ser = WebsiteBookingSerializer(data={**BASE, **overrides})
    return ser, ser.is_valid()


class AmountsNeverFailValidation(SimpleTestCase):

    def test_currency_symbol_is_salvaged(self):
        """The exact payload from delivery #130500."""
        ser, valid = _run(TotalAmount="$975.55")
        self.assertTrue(valid, ser.errors)
        self.assertEqual(ser.validated_data["TotalAmount"], Decimal("975.55"))
        self.assertEqual(ser.amount_warnings, [])

    def test_shapes_the_websites_actually_send(self):
        for raw, expected in [
            ("$975.55",        Decimal("975.55")),
            ("USD 975.55",     Decimal("975.55")),
            ("975.55 USD",     Decimal("975.55")),
            ("1,200.00",       Decimal("1200.00")),
            ("975,55",         Decimal("975.55")),
            ("1.200,00",       Decimal("1200.00")),
            ("(1,200.00)",     Decimal("-1200.00")),
            ("  $ 1 200.50 ",  Decimal("1200.50")),
            ("-45.00",         Decimal("-45.00")),
            ("0",              Decimal("0")),
            (100,              Decimal("100")),
        ]:
            with self.subTest(raw=raw):
                ser, valid = _run(TotalAmount=raw)
                self.assertTrue(valid, ser.errors)
                self.assertEqual(ser.validated_data["TotalAmount"], expected)

    def test_unreadable_amount_is_empty_and_warns_rather_than_failing(self):
        for raw in ("N/A", "free", "TBC", "--", "$"):
            with self.subTest(raw=raw):
                ser, valid = _run(TotalAmount=raw)
                self.assertTrue(valid, ser.errors)
                self.assertIsNone(ser.validated_data["TotalAmount"])
                self.assertEqual(len(ser.amount_warnings), 1)
                self.assertIn("TotalAmount", ser.amount_warnings[0])

    def test_every_amount_field_is_tolerant(self):
        ser, valid = _run(**{f: "N/A" for f in AMOUNT_FIELDS}, Discount="waived")
        self.assertTrue(valid, ser.errors)
        for f in AMOUNT_FIELDS:
            self.assertIsNone(ser.validated_data[f])
        self.assertEqual(ser.validated_data["Discount"], Decimal("0"))
        self.assertEqual(len(ser.amount_warnings), len(AMOUNT_FIELDS) + 1)

    def test_blank_amount_is_silent(self):
        ser, valid = _run(TotalAmount="", Discount="")
        self.assertTrue(valid, ser.errors)
        self.assertIsNone(ser.validated_data["TotalAmount"])
        self.assertEqual(ser.validated_data["Discount"], Decimal("0"))
        self.assertEqual(ser.amount_warnings, [])

    def test_non_amount_validation_still_rejects(self):
        """Only amounts were made tolerant; nothing else was loosened."""
        ser = WebsiteBookingSerializer(data={"Eventcode": "SFIL", "TotalAmount": "$9"})
        self.assertFalse(ser.is_valid())
        self.assertIn("InvoiceNumber", ser.errors)

        ser = WebsiteBookingSerializer(data={
            **BASE, "Delegates": [{"FirstName": "X", "Email": "not-an-email"}],
        })
        self.assertFalse(ser.is_valid())
        self.assertIn("Delegates", ser.errors)
