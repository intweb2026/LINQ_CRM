"""
fix_ticket_number_types
───────────────────────
Rewrites ticket_numbers whose leading segment is not the row's Type of Ticket.

The canonical form is TYPE-PURPOSE NUMBER ('BX-CEU 10001', utils.build_ticket_number).
Rows migrated from Zoho carry the PRIORITY there instead — 'SPEX-PPTX 10037' on a
row whose purpose is PPTX and whose type is Blue - BX — so the type is missing
from the number while the priority, which already has its own column, sits in it.

Only the prefix moves. The trailing number is copied across untouched, so no
series is renumbered and TicketSequence is not consulted.

    python manage.py fix_ticket_number_types              # dry run, prints a plan
    python manage.py fix_ticket_number_types --apply      # writes

Dry run is the default here, unlike backfill_ticket_numbers next door: that one
only fills blanks, this one edits numbers people have already quoted in email.
"""
import csv
import logging
import re
from datetime import datetime

from django.core.management.base import BaseCommand

from ticket_central.models import Ticket
from ticket_central.utils import (
    build_ticket_number, extract_purpose_code, extract_type_code,
)

logger = logging.getLogger(__name__)

TYPE_CODES = frozenset(Ticket.TypeOfTicket.values)

# The trailing token of a well-formed number: 10037, and the handful of legacy
# '12A' / '3B' suffixes the Zoho data carries. Anything else — 'Delete' is 796
# rows of it — is not a number this command can rebuild.
_TAIL_RE = re.compile(r"^\d+[A-Za-z]?$")


def retype(ticket_number, purpose, type_of_ticket):
    """
    The number rebuilt as TYPE-PURPOSE NUMBER, or None to leave the row alone.

    None when: the row has no usable type or purpose, the stored number has no
    numeric tail to carry over, or the prefix is already right.
    """
    code = extract_type_code(type_of_ticket)
    purpose_code = extract_purpose_code(purpose)
    if code not in TYPE_CODES or not purpose_code:
        return None

    head, _, tail = (ticket_number or "").rpartition(" ")
    if not head or not _TAIL_RE.match(tail):
        return None
    # Any type code in the lead segment counts as "has a type". A handful of rows
    # carry one that no longer matches their Type of Ticket column; those are a
    # different question from a missing type, so they are left alone.
    if head.split("-")[0] in TYPE_CODES:
        return None

    rebuilt = build_ticket_number(purpose_code, tail, code)
    return rebuilt if rebuilt != ticket_number else None


class Command(BaseCommand):
    help = "Put the Type of Ticket into ticket_numbers that are missing it."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the changes. Without it the command only reports.",
        )
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Stop after N rows (for a trial run).",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        limit = options["limit"]

        rows = (
            Ticket.objects
            .exclude(ticket_number="")
            .exclude(type_of_ticket="")
            .values_list("id", "ticket_number", "purpose", "type_of_ticket")
            .order_by("id")
        )

        planned, skipped = [], 0
        for pk, number, purpose, type_of_ticket in rows.iterator(chunk_size=2000):
            new_number = retype(number, purpose, type_of_ticket)
            if new_number is None:
                skipped += 1
                continue
            planned.append((pk, number, new_number))
            if limit and len(planned) >= limit:
                break

        for pk, old, new in planned[:20]:
            self.stdout.write(f"  {pk:>7}  {old:<28} -> {new}")
        if len(planned) > 20:
            self.stdout.write(f"  ... and {len(planned) - 20} more")

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN: would rewrite {len(planned)} ticket numbers, "
                f"leave {skipped} unchanged. Re-run with --apply to write."
            ))
            return

        # The old prefixes are not recoverable from anything else once written,
        # so they go to a CSV first — id,old,new is enough to put every row back.
        backup = f"ticket_number_retype_{datetime.now():%Y%m%d-%H%M%S}.csv"
        with open(backup, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["id", "old_ticket_number", "new_ticket_number"])
            writer.writerows(planned)
        self.stdout.write(f"Rollback data written to {backup}")

        # update() rather than save(): ticket_number is the only column moving,
        # and save() would also recompute link_key and re-upper the purpose on
        # 2,000-odd rows this command has no business touching.
        for pk, _old, new in planned:
            Ticket.objects.filter(pk=pk).update(ticket_number=new)

        self.stdout.write(self.style.SUCCESS(
            f"Done. Rewritten: {len(planned)}. Unchanged: {skipped}."
        ))
        logger.info(
            "fix_ticket_number_types complete: rewritten=%s unchanged=%s",
            len(planned), skipped,
        )
