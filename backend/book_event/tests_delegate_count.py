"""
book_event/tests_delegate_count.py
───────────────────────────────────
The correlated-subquery delegate count equals the COUNT(DISTINCT) it replaced.

WHY THIS IS A TEST AND NOT AN ASSERTION IN THE COMMIT MESSAGE
Count("delegates", distinct=True) and a correlated Subquery differ in two ways
that are invisible until they bite:

  1.  A subquery returns NULL for an invoice with NO delegates, where COUNT
      returned 0. Coalesce(..., 0) covers that, and the zero-delegate invoices
      in this fixture are what proves it — the production snapshot happens to
      have none, so a fixture that mirrored it would test nothing.

  2.  BookDelegate.invoice is a ForeignKey with to_field="invoice_number" and
      db_column="invoice_number", so the attname invoice_id holds a varchar
      invoice NUMBER, not an integer pk. OuterRef("pk") would compare varchar to
      integer. The correlation therefore has to be OuterRef("invoice_number"),
      and the filter keyword has to be the attname invoice_id — invoice_number
      is the DB column and Django will not resolve it as a query name.

The comparison below is row by row over 200+ invoices rather than on totals,
because two different wrong answers can sum to the right total.
"""
from django.contrib.auth import get_user_model
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.test import TestCase

from book_delegate.models import BookDelegate
from book_event.models import BookEvent

User = get_user_model()

N_INVOICES = 220
N_EMPTY = 40          # invoices deliberately given no delegates at all


def subquery_annotation(qs):
    """Exactly the annotation BookEventViewSet.get_queryset() applies."""
    counts = (BookDelegate.objects
              .filter(invoice_id=OuterRef("invoice_number"))
              .order_by().values("invoice_id")
              .annotate(n=Count("pk")).values("n")[:1])
    return qs.annotate(_delegate_count_actual=Coalesce(
        Subquery(counts, output_field=IntegerField()), 0))


class DelegateCountEquivalenceTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        invoices = []
        for i in range(N_INVOICES):
            invoices.append(BookEvent.objects.create(
                invoice_number=f"DC-{i:04d}",
                event_code="DC - AA",
                request_date="2026-01-01",
            ))
        # A deliberately uneven spread, including 0, 1 and many, so an
        # off-by-one or a collapsed GROUP BY shows up somewhere.
        for i, inv in enumerate(invoices):
            if i < N_EMPTY:
                continue
            for d in range(i % 5 + 1):
                BookDelegate.objects.create(
                    invoice=inv, event_code="DC - AA",
                    first_name=f"D{d}", email=f"d{i}_{d}@example.com",
                )

    def test_the_fixture_really_contains_zero_delegate_invoices(self):
        """
        Guard on the fixture itself. If this ever stops being true the
        equivalence test below silently stops covering the Coalesce branch,
        which is the only branch where the two expressions genuinely differ.
        """
        empty = (BookEvent.objects
                 .annotate(n=Count("delegates"))
                 .filter(n=0).count())
        self.assertEqual(empty, N_EMPTY)
        self.assertGreaterEqual(BookEvent.objects.count(), 200)

    def test_subquery_matches_count_distinct_row_by_row(self):
        old = dict(BookEvent.objects
                   .annotate(c=Count("delegates", distinct=True))
                   .values_list("pk", "c"))
        new = dict(subquery_annotation(BookEvent.objects.all())
                   .values_list("pk", "_delegate_count_actual"))

        self.assertEqual(set(old), set(new), "the two annotations returned different pks")

        mismatches = {k: (old[k], new[k]) for k in old if old[k] != new[k]}
        self.assertEqual(
            mismatches, {},
            f"{len(mismatches)} invoice(s) disagree between "
            f"Count(distinct=True) and the subquery",
        )

    def test_zero_delegate_invoices_report_0_and_not_none(self):
        """
        The Coalesce, specifically. Without it these serialise as null and the
        Delegates column renders empty instead of 0.
        """
        values = (subquery_annotation(BookEvent.objects.all())
                  .filter(invoice_number__lt=f"DC-{N_EMPTY:04d}")
                  .values_list("_delegate_count_actual", flat=True))
        self.assertEqual(len(values), N_EMPTY)
        self.assertTrue(all(v == 0 for v in values), sorted(set(values)))
        self.assertNotIn(None, values)

    def test_the_correlation_compares_invoice_number_to_invoice_number(self):
        """
        Reads the compiled SQL. The to_field FK means the join column is a
        varchar; if this ever regresses to OuterRef("pk") the correlation would
        compare book_delegates.invoice_number to book_events.id.
        """
        sql = str(subquery_annotation(BookEvent.objects.all()).query)
        self.assertIn('U0."invoice_number" = ("book_events"."invoice_number")', sql)
        self.assertNotIn('U0."invoice_number" = ("book_events"."id")', sql)
