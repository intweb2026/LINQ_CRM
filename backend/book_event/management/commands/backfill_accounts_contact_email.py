"""
Fill every blank invoice Accounts Contact Email from its delegates.

    python manage.py backfill_accounts_contact_email            # dry run
    python manage.py backfill_accounts_contact_email --apply    # commit

DRY RUN BY DEFAULT, as backfill_sales_executives is: this writes a column that
finance reads, and a run nobody meant to make should cost nothing.

Only BLANK accounts contacts are touched, so the command is idempotent and a
second run reports nothing left to do. The logic lives in
book_delegate/accounts_contact.py, which is also what fills the column on every
delegate write from now on.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from book_delegate.accounts_contact import backfill_accounts_contact_emails


class Command(BaseCommand):
    help = (
        "Copy the delegate's email into the invoice's Accounts Contact Email "
        "wherever that is blank. Dry-run unless --apply is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Commit the updates. Without this flag, nothing is written.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Invoices per query batch (default 500).",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        # One transaction, so an interrupted run leaves the history either fully
        # backfilled or untouched — never half-filled from whichever batch it
        # reached, which is indistinguishable afterwards from a genuine gap.
        with transaction.atomic():
            stats = backfill_accounts_contact_emails(
                apply=apply, batch_size=options["batch_size"]
            )

        self.stdout.write(f"Invoices with a blank accounts contact: {stats['scanned']}")
        self.stdout.write(f"  fillable from a delegate email:      {stats['updated']}")
        self.stdout.write(f"  no delegate email to use:            {stats['no_delegate_email']}")
        if apply:
            self.stdout.write(self.style.SUCCESS(f"Updated {stats['updated']} invoice(s)."))
        else:
            self.stdout.write(self.style.WARNING("Dry run — nothing written. Re-run with --apply."))
