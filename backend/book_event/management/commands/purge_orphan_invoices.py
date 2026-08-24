"""
purge_orphan_invoices.py
────────────────────────
Delete BookEvent rows that have no delegates left.

WHAT THESE ROWS ARE
Until book_delegate/views.py bulk_delete was fixed, deleting every delegate on a
booking removed the delegate rows and left the invoice behind. That invoice is
invisible in the Bookings table — the table lists delegates — while remaining
very much present everywhere else:

  · it still counts toward the dashboard aggregates that read BookEvent
  · it is still exported by the Data API's `bookings` resource, so it keeps
    reappearing in the Google Sheet after being "deleted" in the CRM
  · it still holds its unique invoice_number, so the webhook and the importers,
    which all upsert on invoice_number, match it and re-create its delegates

That last point is what made deletes look like they were being undone. Nothing
was soft-deleted; the invoice was never deleted.

DRY RUN BY DEFAULT
Prints what it would delete and writes nothing. Pass --commit to delete.

    python manage.py purge_orphan_invoices
    python manage.py purge_orphan_invoices --commit

--since limits the sweep to invoices created on or after a date, for when only
a recent delete needs undoing rather than the whole history.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Sum

from book_event.models import BookEvent


class Command(BaseCommand):
    help = "Delete invoices left with no delegates (dry run unless --commit)."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true",
                            help="Actually delete. Without this, nothing is written.")
        parser.add_argument("--since", default=None,
                            help="Only invoices created on or after this date (YYYY-MM-DD).")
        parser.add_argument("--limit", type=int, default=25,
                            help="How many rows to list in the report (default 25).")

    def handle(self, *args, **options):
        qs = BookEvent.objects.annotate(n=Count("delegates")).filter(n=0)
        if options["since"]:
            qs = qs.filter(created_at__date__gte=options["since"])

        total = qs.count()
        if not total:
            self.stdout.write(self.style.SUCCESS("No orphaned invoices. Nothing to do."))
            return

        money = qs.aggregate(s=Sum("total_amount"))["s"] or 0
        self.stdout.write(f"Invoices with zero delegates: {total}")
        self.stdout.write(f"Their total_amount sums to:   {money}")
        self.stdout.write("")
        for number, code, amount, source in qs.values_list(
                "invoice_number", "event_code", "total_amount", "source")[:options["limit"]]:
            self.stdout.write(f"  {number:<24} {code:<16} {amount!s:<12} {source}")
        if total > options["limit"]:
            self.stdout.write(f"  … and {total - options['limit']} more")
        self.stdout.write("")

        if not options["commit"]:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing written. Re-run with --commit to delete these."))
            return

        # Re-resolved inside the transaction: the annotated queryset is not a
        # reliable thing to call .delete() on, and the count is worth taking
        # against the same snapshot the delete runs on.
        with transaction.atomic():
            numbers = list(qs.values_list("invoice_number", flat=True))
            deleted, _ = BookEvent.objects.filter(invoice_number__in=numbers).delete()
        self.stdout.write(self.style.SUCCESS(
            f"Deleted {len(numbers)} invoices ({deleted} rows including cascades)."))
