"""
credit_control/signals.py
──────────────────────────
LIVE ROUTING. An invoice is placed on somebody's queue the moment it is saved,
rather than waiting for the next pass.

WHY A SIGNAL AND NOT THE WEBHOOK. The website webhook is not the only writer:
`load_zoho_export`, the bulk-update engine, the Bookings modal, the DRF
serializers and the Django admin all save invoices too. Hooking the webhook
would make website bookings live and leave every other path waiting for cron,
which is the kind of split that takes a week to notice. `BookEvent.save()` is
the chokepoint every one of them goes through, which is the same reasoning the
model itself gives for canonicalising booking codes there.

WHAT IS AND IS NOT LIVE, because the three triggers are genuinely different:

  A NEW INVOICE is an event, so it is live. It becomes a lead and lands on the
  team lead's queue as the booking is created.

  A PAYMENT OR STATUS CHANGE is an event, so it is live. The invoice is saved,
  and the lead leaves the queue for Resolved on that save.

  THE DAY-4 HANDOFF IS NOT AN EVENT. Nothing happens; a date rolls over. There
  is no save to hang it off, so it needs a clock, and an hourly pass is what
  covers it. A lead crossing four days does so at midnight UTC, so hourly means
  it moves within the hour rather than at the end of the next shift.

  A save DOES re-evaluate the handoff for that one invoice, so anything touched
  is fully current immediately; the hourly pass exists for the leads nobody
  touches.

SAFETY. Three rules, all of them earned rather than defensive:

  1. It never breaks a save. Everything is caught and logged. A booking must be
     recordable even if Credit Control's routing is broken.
  2. It can be suspended, and the importers do. `load_zoho_export` writes
     thousands of invoices in one run, and routing each one individually would
     turn one import into thousands of small transactions.
  3. It is idempotent, because it calls the same `place_invoice` the full pass
     calls. Running twice on one invoice changes nothing the second time.
"""
import logging
from contextlib import contextmanager

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)

# Set while a bulk writer is running. Not thread-local on purpose: the importers
# are management commands running in their own process, and a request-serving
# worker never sets it, so there is nothing to isolate between threads.
_suspended = False


@contextmanager
def suspend_routing():
    """
    Turn live routing off for a block, for bulk writers.

    `load_zoho_export` saves thousands of invoices in one run. Routing each as
    it lands would be thousands of separate transactions to reach the state one
    pass reaches in a second, so the importer suspends this and the hourly pass
    picks the batch up. Any importer added later should do the same.
    """
    global _suspended
    was = _suspended
    _suspended = True
    try:
        yield
    finally:
        _suspended = was


@receiver(post_save, sender="book_event.BookEvent",
          dispatch_uid="credit_control.route_on_invoice_save")
def route_on_invoice_save(sender, instance, **kwargs):
    """
    Place one invoice as it is saved.

    Deferred with transaction.on_commit, which matters twice over: the routing
    reads the invoice back, so it must not run before the row is durable, and a
    save that is rolled back must not leave a lead behind describing an invoice
    that never existed.
    """
    if _suspended:
        return

    invoice_number = getattr(instance, "invoice_number", None)
    if not invoice_number:
        return

    def _route():
        try:
            route_invoice(invoice_number)
        except Exception:  # noqa: BLE001
            # A booking has to be recordable even when Credit Control is broken.
            # Logged with the invoice so it can be found, and the hourly pass
            # will place it anyway.
            logger.exception(
                "credit_control: live routing failed for %s; the next pass will "
                "place it", invoice_number,
            )

    transaction.on_commit(_route)


def route_invoice(invoice_number: str, *, now=None) -> str:
    """
    Route exactly one invoice. Returns the bucket it landed in, or "".

    Reuses `place_invoice`, so this and the full pass apply one copy of the five
    rules. What it deliberately does NOT do is the closing sweep: deciding that
    a lead has vanished from the source needs the whole source, and one invoice
    is not that. A status change away from Pending is still handled, because the
    invoice is saved and this reads its live status.
    """
    from book_event.models import BookEvent

    from . import engine
    from .models import CreditControlLead

    now = now or timezone.now()
    invoice = (
        BookEvent.objects
        .filter(invoice_number=invoice_number)
        .only(
            "invoice_number", "event_code", "booking_code", "invoice_date",
            "payment_date", "payment_status", "company_name",
        )
        .first()
    )
    if invoice is None:
        return ""

    lead = (
        CreditControlLead.objects
        .select_related("assigned_to")
        .filter(pk=invoice_number)
        .first()
    )

    # An invoice that is not Pending is not chased. If it HAS a lead, it is
    # still worth placing, because that is how a lead reaches Resolved the
    # moment somebody marks it paid.
    pending = (invoice.payment_status or "").strip() == BookEvent.PaymentStatus.PENDING
    if not pending:
        if lead is None:
            return ""
        # Through the same stamp the pass uses, so a payment marked in the
        # Bookings UI credits its owner exactly as a nightly one would.
        engine._mark_resolved(
            lead, invoice, groups=engine.status_group_map(), now=now,
        )
        return lead.bucket

    roster = engine.Roster.load()
    engine.place_invoice(
        invoice, lead,
        roster=roster,
        groups=engine.status_group_map(),
        excluded=engine.excluded_event_codes(),
        balancer=engine.PoolBalancer(roster.pool),
        summary={
            "active": 0, "not_invoiced": 0, "spex": 0, "done": 0, "excluded": 0,
            "created": 0, "handed_off": 0, "reactivated": 0, "unassigned": 0,
        },
        now=now,
        today=now.date(),
    )
    placed = CreditControlLead.objects.filter(pk=invoice_number).first()
    return placed.bucket if placed else ""
