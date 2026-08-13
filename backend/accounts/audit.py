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
