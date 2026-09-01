"""
accounts/audit.py
──────────────────
The audit entry every module wipe writes.

WHY IT IS SHARED
"Clear all data" is the only action in the CRM that can destroy a whole module, and
five endpoints now offer it (bookings, events, ticket central, paper review,
proposal submission). Each was free to log it — or not: two of the three that
existed wrote no ActionLog at all, so a wipe of every invoice in the database left
nothing behind saying who did it or when. An irreversible action with no record of
who ran it is the one case where a missing log is itself the incident.

One helper means the entry reads the same for every module, and adding a sixth wipe
cannot forget to write one.
"""
import logging

logger = logging.getLogger(__name__)


def log_module_wipe(user, module_label, deleted):
    """
    Record that `user` emptied `module_label`. `deleted` maps table → row count.

    Also logged at WARNING to the application log: ActionLog rows live in the same
    database the wipe just emptied, and while none of these endpoints touch the
    action_logs table, a wipe is worth a line somewhere that survives a bad restore.
    """
    counts = ", ".join(f"{name}={count}" for name, count in sorted(deleted.items()))
    logger.warning("MODULE WIPE: %s cleared %s (%s)", user.username, module_label, counts)

    from accounts.models import ActionLog
    return ActionLog.objects.create(
        user=user,
        action=f"CLEARED ALL {module_label} DATA",
        details=f"Deleted {counts or 'nothing'}.",
    )


def reclaim_after_wipe(*tables):
    """
    Give the space a wipe just freed back to PostgreSQL, and re-collect statistics.

    WHY A WIPE NEEDS THIS
    A DELETE does not remove rows, it marks them dead, and the pages they occupied
    stay in the table file. A sequential scan still reads those pages, so a table
    that has been filled and emptied a few times reads like a large table however
    few rows it now holds. MEASURED on this database after several clear-and-import
    cycles: book_delegates held about 1,250 rows in 550 MB, and every query over it
    paid for all 550 MB. The delegate list took 171 ms, its bare COUNT 181 ms, and
    the dashboard aggregate — four GROUP BYs over that same table — 507 ms. After
    reclaiming, the same three were 14 ms, 11 ms and 62 ms.

    Autovacuum does not rescue this on its own. It reclaims for REUSE, so the file
    never shrinks, and on a development database that is often stopped it may not
    have run at all: none of these tables had ever been analyzed, which also left
    the planner choosing plans with no statistics to go on.

    WHY IT IS SAFE HERE
    VACUUM FULL rewrites the table and takes an exclusive lock for the duration,
    which would be the wrong tool on a busy table. This runs immediately after that
    table was emptied, so there is nothing to rewrite and the lock is held for
    milliseconds. It must run OUTSIDE any transaction — VACUUM cannot run inside
    one — so call it after the atomic block, never within it.

    A failure here is logged and swallowed. The wipe itself has already committed
    and succeeded; refusing the response because a maintenance step did not run
    would report a destructive action as failed when it did not fail.

    IT MUST ALSO NOT BE ATTEMPTED INSIDE A TRANSACTION, which is stronger than
    "must not run" and is why in_atomic_block is checked rather than relying on the
    try/except below. In PostgreSQL a failed statement aborts the ENTIRE
    transaction, so catching the "VACUUM cannot run inside a transaction block"
    error still leaves a connection on which every later statement answers "current
    transaction is aborted". Swallowing it was measurably worse than not trying:
    15 tests in accounts/tests_clear_all_gate.py went from passing to erroring,
    because Django's TestCase wraps each test in a transaction and the poisoned
    connection took out the assertions that ran after the wipe. Skipping is silent
    and harmless — a wipe inside a transaction is a test, and a test database is
    dropped rather than vacuumed.
    """
    from django.db import connection

    if connection.vendor != "postgresql":
        return []
    if connection.in_atomic_block:
        logger.debug("reclaim_after_wipe: inside a transaction, skipping %s", tables)
        return []
    done = []
    for table in tables:
        try:
            with connection.cursor() as cur:
                # Identifier quoted, and these are module-level literals from the
                # callers, never request data.
                cur.execute(f'VACUUM (FULL, ANALYZE) "{table}"')
            done.append(table)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reclaim_after_wipe: %s could not be vacuumed (%s)", table, exc)
    return done


def log_import_batch(user, module_label, batch_id, counts, detail=""):
    """
    Record that `user` ran an import, and under which batch identifier.

    WHY THIS EXISTS
    `load_zoho_export` stamped an import_batch_id on every row it wrote and said
    so in its output; the browser importer stamped nothing and wrote no audit
    record at all. So when the 26 August master import lost or flattened seven
    columns, nothing in the database marked a row as belonging to it. The invoice
    timestamps could not stand in either, because the importer BACKDATES
    created_at from the file wherever an Added Time column is mapped.

    Scoping a repair then meant guessing across 11,288 invoices. With this, the
    rows an import wrote can be listed from the identifier alone:

        BookEvent.objects.filter(import_batch_id=batch_id)
        BookDelegate.objects.filter(import_batch_id=batch_id)

    `user` may be anonymous — the caller is an authenticated endpoint, but a
    management command or a test client is not, and refusing to log is worse than
    logging without a name, so the ActionLog row is skipped and the application
    log still gets the line.
    """
    summary = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
    logger.info(
        "IMPORT BATCH: %s into %s by %s (%s)%s",
        batch_id, module_label,
        getattr(user, "username", None) or "unauthenticated",
        summary, f" — {detail}" if detail else "",
    )

    if not getattr(user, "is_authenticated", False):
        return None

    from accounts.models import ActionLog
    return ActionLog.objects.create(
        user=user,
        action=f"IMPORTED {module_label}",
        details=f"batch_id={batch_id}; {summary}" + (f"; {detail}" if detail else ""),
    )
