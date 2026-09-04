"""
backfill_ticket_numbers
───────────────────────
Assigns ticket_numbers to tickets that don't have one yet.
Mirrors the Zoho Deluge BackfillTicketNumbers_Batch job.

Run daily at 07:00 IST (01:30 UTC) via cron, or on demand by admin.

Numbers come from utils.assign_next_ticket_number, the same generator the API
and Smart Import use. This command used to carry its own copy: it read every
TicketSequence into a dict up front, did `last_number + 1` per row from that
snapshot, and wrote the counters back at the end. Nothing locked the sequence
rows, so a run overlapping a UI create handed out numbers the create had already
taken. D6's "sequence updates once per run, at the end" is what caused it, so
that is gone; each row now takes the row lock the shared generator takes.
"""
import logging

from django.core.management.base import BaseCommand

from ticket_central.models import Ticket
from ticket_central.utils import extract_purpose_code, assign_next_ticket_number

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Assign ticket_numbers to tickets that don't have one yet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would happen but don't save.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        pending = Ticket.objects.filter(ticket_number__exact="").order_by("created_at")

        total_found = 0
        total_skipped = 0

        for ticket in pending.iterator(chunk_size=500):
            purpose_code = extract_purpose_code(ticket.purpose)
            if not purpose_code:
                total_skipped += 1
                logger.info("Skipped ticket id=%s — no Purpose", ticket.id)
                continue

            total_found += 1
            if dry_run:
                continue

            # ponytail: one locked round-trip per row. The queue is rows that
            # arrived without a number, so it is small; batch by purpose if a
            # migration ever makes it large.
            ticket.ticket_number = assign_next_ticket_number(
                purpose_code, ticket.type_of_ticket,
            )
            ticket.save(update_fields=["ticket_number"])

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN: would number {total_found} tickets, "
                f"skip {total_skipped} with no Purpose"
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Done. Numbered: {total_found}. "
            f"Skipped (no Purpose): {total_skipped}."
        ))
        logger.info(
            "backfill_ticket_numbers complete: found=%s skipped=%s",
            total_found, total_skipped,
        )
