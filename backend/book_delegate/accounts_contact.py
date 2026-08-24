"""
book_delegate/accounts_contact.py
─────────────────────────────────
Accounts Contact Email, where the invoice has none of its own.

Accounts Contact is who an invoice is chased with. It lives on the INVOICE
(BookEvent.accounts_contact_email), and a large part of the imported history has
it blank — not because those bookings have no billing contact, but because the
column was never filled in. The delegate's own email is the only address anybody
had for that booking, so a blank accounts contact takes it.

TWO HALVES, and they do the same thing at different times:

  fill_accounts_contact_from_delegate()  runs on every delegate WRITE, so a
                                         booking created from now on never sits
                                         with a blank accounts contact.
  backfill_accounts_contact_emails()     walks the rows that already exist and
                                         fills them once. Driven by
                                         `manage.py backfill_accounts_contact_email`.

Both write a REAL value into the invoice column rather than deriving one on the
fly, so it is exportable, searchable and editable like any other stored field —
and editable is the point: a sales exec who sets a proper billing address is
never overwritten, because everything here only ever touches a BLANK column.

book_delegate/serializers.py keeps its read-time fallback on top of this. Rows
written by paths that bypass model save() (the Excel importer uses bulk_create)
still read correctly before anyone runs the backfill.
"""
from django.db.models import Q

# A blank accounts contact is stored as '' by the model default, and as NULL by
# some of the imported history. Both mean "nobody filled this in".
BLANK_ACCOUNTS_CONTACT = Q(accounts_contact_email="") | Q(accounts_contact_email__isnull=True)


def fill_accounts_contact_from_delegate(delegate) -> bool:
    """
    Copy `delegate`'s email onto its invoice's accounts contact, if that is blank.

    Returns whether a row was written. Called from BookDelegate.save().

    Written as a conditional UPDATE rather than read-then-write: the blank test
    is in the WHERE clause, so two delegates saved concurrently cannot both
    decide the column is empty, and a filled column is never touched. It also
    means no SELECT and no BookEvent.save() — the invoice's own derivations
    (event_name rebuilding, edition parsing) have nothing to do with this column
    and must not fire on the back of a delegate write.
    """
    from book_event.models import BookEvent

    email = (delegate.email or "").strip()
    if not email or not delegate.invoice_id:
        return False

    # The invoice is already in memory on the paths that select_related it, so
    # the common case — a booking whose accounts contact is set — costs nothing.
    cached = delegate._state.fields_cache.get("invoice")
    if cached is not None and (cached.accounts_contact_email or "").strip():
        return False

    written = (
        BookEvent.objects
        .filter(invoice_number=delegate.invoice_id)
        .filter(BLANK_ACCOUNTS_CONTACT)
        .update(accounts_contact_email=email)
    )
    # Keep the in-memory invoice in step with the row just written, or the
    # response built from this same object would still report it blank.
    if written and cached is not None:
        cached.accounts_contact_email = email
    return bool(written)


def backfill_accounts_contact_emails(*, apply=False, batch_size=500):
    """
    Fill every blank invoice accounts contact from its delegates.

    Returns {"scanned", "updated", "no_delegate_email"} — with apply=False
    nothing is written and "updated" is what WOULD have been.

    Which delegate's email? The FIRST on the invoice: lowest delegate number,
    and the earliest row where those tie. An invoice with several delegates has
    one billing contact, and delegate 1 is the booking's own contact — picking
    arbitrarily would make a re-run against restored data disagree with itself.
    """
    from book_event.models import BookEvent
    from .models import BookDelegate

    invoice_numbers = list(
        BookEvent.objects.filter(BLANK_ACCOUNTS_CONTACT)
        .values_list("invoice_number", flat=True)
    )
    stats = {"scanned": len(invoice_numbers), "updated": 0, "no_delegate_email": 0}

    for start in range(0, len(invoice_numbers), batch_size):
        chunk = invoice_numbers[start:start + batch_size]

        # Ordered WORST-first so the dict ends up holding the best candidate:
        # each assignment overwrites the last, and delegate 1 is written last.
        # NOTE: invoice_id holds the invoice NUMBER — the FK is declared
        # to_field="invoice_number" (models.py) — so it joins to the chunk above
        # directly and this needs no second lookup.
        best = {}
        rows = (
            BookDelegate.objects
            .filter(invoice_id__in=chunk)
            .exclude(email="")
            .exclude(email__isnull=True)
            .order_by("-delegate_number", "-id")
            .values_list("invoice_id", "email")
        )
        for invoice_number, email in rows:
            email = (email or "").strip()
            if email:
                best[invoice_number] = email

        stats["no_delegate_email"] += len(chunk) - len(best)
        if not best:
            continue
        stats["updated"] += len(best)
        if not apply:
            continue

        # bulk_update, so BookEvent.save() does not run: this is one column, and
        # a backfill has no business re-deriving event names across the history.
        invoices = list(BookEvent.objects.filter(invoice_number__in=list(best)))
        for inv in invoices:
            inv.accounts_contact_email = best[inv.invoice_number]
        BookEvent.objects.bulk_update(invoices, ["accounts_contact_email"], batch_size=batch_size)

    return stats
