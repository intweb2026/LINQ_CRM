"""
book_event/booking_code_repair.py
─────────────────────────────────
The LIVE-DATA half of booking_code_canonical.py.

canonicalize() fixes every booking_code written from now on. It cannot fix the
rows already stored — the webhook has been stamping lowercase "delegate" on
bookings for as long as that literal has been in webhooks/services.py, and those
rows sit in production spelled that way. This module rewrites them, and is used
by BOTH the management command and the data migration so a manual run and a
deploy do exactly the same thing.

WHY .update() AND NOT save()
The project rule is per-object save(), because BookDelegate.save() and
BookEvent.save() are load-bearing. They are load-bearing in ways that are ACTIVELY
UNWANTED here: BookEvent.save() re-parses event_code, strips a trailing year into
`edition`, and REWRITES event_name from the Event catalogue. Running it across
every stored row to change the capitalisation of one unrelated column would put
hundreds of rows' event_name and edition through a derivation they were never
asked to re-run — a far larger blast radius than the fix. This writes one
column, runs no derivation, and is scoped by an exact-value filter, which is the
carve-out BookEvent.save() itself documents for its own .update().

WHY EXACT-MATCH FILTERS AND NOT A SWEEP
Rows are grouped by their stored spelling and each group is updated with
filter(booking_code=<stored exact>). Nothing is matched loosely, nothing is
matched by pattern, and a row whose stored value canonicalize() does not
recognise is never in any group, so it is never written. On a database that is
already canonical this issues zero UPDATEs.

IDEMPOTENT
Running it twice changes nothing the second time: after the first pass every
stored value equals its own canonical spelling, so no group is produced. That is
what makes it safe as a migration AND as a cron/manual repair for anything that
slips in later.
"""
from collections import Counter

from book_event.booking_code_canonical import canonicalize


def plan(model):
    """
    [(stored_spelling, canonical_spelling, row_count), ...] for every stored
    booking_code whose spelling is not already canonical, commonest first.

    One cheap grouped query — the column is low-cardinality (about 15 distinct
    values over ~1k rows), so this reads a handful of rows, not the table.
    """
    counts = Counter()
    for stored in (model.objects
                   .exclude(booking_code="")
                   .values_list("booking_code", flat=True)
                   .order_by()
                   .distinct()):
        target = canonicalize(stored)
        if target != stored:
            counts[(stored, target)] = model.objects.filter(booking_code=stored).count()
    return [(stored, target, n)
            for (stored, target), n in counts.most_common()]


def repair(model, apply=False):
    """
    Apply `plan(model)`. Returns the plan, with the counts it actually wrote
    (or would have written, when apply is False).
    """
    entries = plan(model)
    if apply:
        for stored, target, _ in entries:
            model.objects.filter(booking_code=stored).update(booking_code=target)
    return entries


def repair_all(models, apply=False):
    """{model_label: plan} across several models, in the order given."""
    return {m._meta.label: repair(m, apply=apply) for m in models}
