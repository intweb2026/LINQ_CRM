"""
audit_booking_counts.py
───────────────────────
Read-only. Explains the gap between what the Bookings screen counts and what
the Data API exports.

WHY THE TWO NUMBERS ARE NOT THE SAME THING
The Bookings screen lists DELEGATES, one row per person. The Data API's
`bookings` resource exports BookEvent, one row per INVOICE. They are different
tables, so they were never going to agree, and neither is wrong.

The gap becomes a problem when it is made of invoices that have no delegates
left. Those are the residue of deletes made before bulk_delete removed the
emptied invoice: invisible on the Bookings screen, still exported to the sheet,
still counted by the dashboard, and still holding an invoice_number that the
webhook and the importers will upsert onto and repopulate.

    python manage.py audit_booking_counts

Writes nothing. Use purge_orphan_invoices to act on what this reports.
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, Sum

from book_delegate.models import BookDelegate
from book_event.models import BookEvent


class Command(BaseCommand):
    help = "Report invoice vs delegate counts and orphaned invoices. Read-only."

    def handle(self, *args, **options):
        invoices  = BookEvent.objects.count()
        delegates = BookDelegate.objects.count()
        orphans   = BookEvent.objects.annotate(n=Count("delegates")).filter(n=0)
        orphan_count = orphans.count()
        orphan_money = orphans.aggregate(s=Sum("total_amount"))["s"] or 0

        # invoice_number is unique=True, so this can only ever be zero. Asserted
        # rather than assumed, because the question being answered is "why does
        # the sheet hold each booking more than once", and ruling the database
        # out is half the answer.
        dupes = (BookEvent.objects
                 .values("invoice_number")
                 .annotate(n=Count("id"))
                 .filter(n__gt=1)
                 .count())

        # The FK is db_constraint=False, so nothing in the database enforces this.
        invoice_numbers = set(BookEvent.objects.values_list("invoice_number", flat=True))
        stranded = sum(
            1 for number in BookDelegate.objects.values_list("invoice_id", flat=True)
            if number not in invoice_numbers
        )

        w = self.stdout.write
        w("")
        w(f"  invoices  (book_events, what the Data API exports) : {invoices}")
        w(f"  delegates (book_delegates, what the screen counts) : {delegates}")
        w("")
        w(f"  invoices with NO delegates left                    : {orphan_count}")
        w(f"     their total_amount sums to                      : {orphan_money}")
        w(f"  duplicate invoice_numbers in the database          : {dupes}")
        w(f"  delegates whose invoice is missing                 : {stranded}")
        w("")

        if dupes:
            w(self.style.ERROR(
                "  Duplicate invoice_numbers exist. That should be impossible on a "
                "unique column; investigate before syncing anything."))
        else:
            w("  Each booking exists exactly once here, so anything repeated in the")
            w("  sheet was written there by the sync, not released twice by the CRM.")

        if orphan_count:
            w("")
            w(self.style.WARNING(
                f"  {orphan_count} invoices would still be exported to the sheet while "
                f"showing nowhere on the Bookings screen."))
            w("  Run: python manage.py purge_orphan_invoices          (dry run)")
            w("       python manage.py purge_orphan_invoices --commit (deletes)")
        w("")
