"""
accounts/management/commands/normalize_booking_codes.py
───────────────────────────────────────────────────────
Bring every stored booking_code to its canonical spelling.

    python manage.py normalize_booking_codes             # report only, writes nothing
    python manage.py normalize_booking_codes --apply     # rewrite the rows

WHAT IT FIXES
The webhook wrote the literal lowercase "delegate" on every booking it created,
while the rest of the product — and every row that predates that path — spells
it "Delegate". One logical code, two stored spellings, showing as two separate
options in the Bookings dropdown. See book_event/booking_code_canonical.py.

The 0028 data migration runs the same repair once, at deploy. This command
exists for the case the migration cannot cover: a spelling that slips in later,
or a canonical list that gains an entry. It is idempotent, so on a clean
database it reports nothing and writes nothing.

SCOPE
Two tables, one column, exact-value filters only. A booking_code the canonical
list does not recognise is never touched — it is not fuzzy-matched, and it is
not blanked.
"""
from django.core.management.base import BaseCommand

from book_delegate.models import BookDelegate
from book_event.booking_code_repair import repair
from book_event.models import BookEvent

MODELS = (BookEvent, BookDelegate)


class Command(BaseCommand):
    help = "Rewrite stored booking_code values to their canonical spelling."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually rewrite the rows. Without this the command only reports.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        total = 0

        for model in MODELS:
            entries = repair(model, apply=apply)
            label = model._meta.label
            if not entries:
                self.stdout.write(f"{label:<28} already canonical")
                continue
            for stored, target, count in entries:
                total += count
                verb = "rewrote" if apply else "would rewrite"
                self.stdout.write(
                    f"{label:<28} {verb} {count:>6} rows  {stored!r} -> {target!r}"
                )

        if not total:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
        elif apply:
            self.stdout.write(self.style.SUCCESS(f"Done — {total} rows rewritten."))
        else:
            self.stdout.write(self.style.WARNING(
                f"{total} rows differ. Re-run with --apply to rewrite them."
            ))
