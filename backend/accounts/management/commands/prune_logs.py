"""
accounts/management/commands/prune_logs.py
───────────────────────────────────────────
Delete log rows older than their retention window.

WHAT THIS EXISTS FOR
webhook_events is 130,304 rows and grows with every integration delivery; it is
the largest table in the CRM and nothing has ever removed a row from it. The
other three are small today and grow without bound for the same reason. Windows
live in settings.LOG_RETENTION_DAYS so changing one is a deployment change, and
every one is env-overridable.

ACTIONLOG IS EXCLUDED BY DESIGN
accounts.ActionLog is the audit trail. It is deliberately absent from
LOG_RETENTION_DAYS, so this command will not delete from it under any flag
combination — `--commit` included. `--report-action-logs` prints what pruning
WOULD remove at 180, 365 and 730 days and exits, so the decision can be made on
numbers by a person rather than by a default in a settings file.

DRY-RUN IS THE DEFAULT, mirroring the bulk_update preview convention already in
this codebase: the destructive path is the one you have to ask for.

    python manage.py prune_logs                      # report only, deletes nothing
    python manage.py prune_logs --commit             # actually delete
    python manage.py prune_logs --report-action-logs # ActionLog what-if, exits

CHUNKED, NEVER ONE STATEMENT
Deletion runs 5,000 rows per statement inside its own transaction.atomic().
A single DELETE over 100,000+ rows holds row locks and accumulates one
transaction's worth of WAL for the whole run, and if it fails at 99% the entire
thing rolls back. Per-chunk transactions mean an interrupted run keeps the work
it finished and re-running simply continues.

RAW SQL IS SAFE HERE, AND THAT WAS CHECKED
Raw chunked DELETE skips Django's collector, which is only correct when nothing
cascades and nothing listens. Verified across the whole codebase: NO model
declares a ForeignKey or OneToOneField to any of webhooks.WebhookLog,
accounts.ActionLog, teams.TeamActivityLog or
paper_review.NotificationLog, and none of them has a pre_delete/post_delete
receiver. Each model's path is printed in the report so the choice is visible at
run time rather than assumed from this docstring.
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models.signals import post_delete, pre_delete
from django.utils import timezone

CHUNK = 5_000

# The column each model is aged by. Chosen explicitly rather than guessed from
# the field list: webhook_events carries four datetimes and only created_at is
# the row's own birth — received_at is the upstream delivery time and is NULL on
# rows that never got that far, which would make them immortal.
AGE_FIELD = {
    "webhooks.WebhookLog":          "created_at",
    "teams.TeamActivityLog":        "created_at",
    "paper_review.NotificationLog": "sent_at",
    "accounts.ActionLog":           "created_at",
}

ACTION_LOG_WHATIF_WINDOWS = (180, 365, 730)


class Command(BaseCommand):
    help = "Delete log rows older than settings.LOG_RETENTION_DAYS. Dry-run by default."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit", action="store_true",
            help="Actually delete. Without this the command only reports.",
        )
        parser.add_argument(
            "--report-action-logs", action="store_true",
            help="Print what ActionLog pruning would remove at 180/365/730 days, "
                 "then exit. Deletes nothing, ever.",
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _model(self, label):
        from django.apps import apps
        return apps.get_model(label)

    def _has_delete_receivers(self, model):
        """
        True if anything listens for this model's deletion.

        Raw SQL bypasses signals, so a receiver would be silently skipped. This
        is checked at RUN TIME rather than trusted from a comment, because a
        receiver added later would otherwise break quietly.
        """
        return bool(pre_delete._live_receivers(model)[0]
                    or post_delete._live_receivers(model)[0])

    def _has_inbound_fks(self, model):
        """
        True if any model points a ForeignKey at this one.

        Raw SQL bypasses Django's cascade collector. With no inbound relations
        there is nothing to cascade and the raw path is equivalent; with any, it
        is not, and the ORM path is used instead.
        """
        return bool([
            r for r in model._meta.related_objects
            if r.field.many_to_one or r.field.one_to_one
        ])

    def _table_size(self, model):
        if connection.vendor != "postgresql":
            return "n/a"
        with connection.cursor() as c:
            c.execute("SELECT pg_size_pretty(pg_total_relation_size(%s))",
                      [model._meta.db_table])
            return c.fetchone()[0]

    def _describe(self, model, field, cutoff):
        """(count, oldest, newest) for the rows outside the window."""
        qs = model.objects.filter(**{f"{field}__lt": cutoff})
        n = qs.count()
        if not n:
            return 0, None, None
        oldest = qs.order_by(field).values_list(field, flat=True).first()
        newest = qs.order_by(f"-{field}").values_list(field, flat=True).first()
        return n, oldest, newest

    # ── Deletion ──────────────────────────────────────────────────────────────

    def _delete_raw(self, model, field, cutoff):
        """
        Chunked raw DELETE. One transaction per chunk.

        The subselect is on the primary key with a LIMIT, so each statement
        touches a bounded set and the planner can use the pk index for the
        delete itself rather than re-scanning on the date column every pass.
        """
        table = model._meta.db_table
        pk = model._meta.pk.column
        col = model._meta.get_field(field).column
        removed = 0
        while True:
            with transaction.atomic():
                with connection.cursor() as c:
                    c.execute(
                        f'DELETE FROM "{table}" WHERE "{pk}" IN ('
                        f'  SELECT "{pk}" FROM "{table}" WHERE "{col}" < %s'
                        f'  ORDER BY "{pk}" LIMIT {CHUNK})',
                        [cutoff],
                    )
                    n = c.rowcount
            removed += n
            if n < CHUNK:
                return removed
            self.stdout.write(f"      ... {removed} deleted so far")

    def _delete_orm(self, model, field, cutoff):
        """Chunked ORM delete, so cascades and signals fire. One txn per chunk."""
        removed = 0
        while True:
            ids = list(model.objects
                       .filter(**{f"{field}__lt": cutoff})
                       .order_by("pk")
                       .values_list("pk", flat=True)[:CHUNK])
            if not ids:
                return removed
            with transaction.atomic():
                n, _ = model.objects.filter(pk__in=ids).delete()
            removed += len(ids)
            if len(ids) < CHUNK:
                return removed
            self.stdout.write(f"      ... {removed} deleted so far")

    # ── Entry point ───────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        now = timezone.now()

        if options["report_action_logs"]:
            return self._report_action_logs(now)

        commit = options["commit"]
        windows = getattr(settings, "LOG_RETENTION_DAYS", {})
        if not windows:
            self.stdout.write(self.style.WARNING(
                "settings.LOG_RETENTION_DAYS is empty. Nothing to do."))
            return

        self.stdout.write(
            "PRUNE LOGS " + ("(COMMIT)" if commit else "(DRY RUN, deletes nothing)"))
        self.stdout.write("=" * 72)

        total = 0
        for label in sorted(windows):
            days = windows[label]
            field = AGE_FIELD.get(label)
            if field is None:
                self.stderr.write(self.style.ERROR(
                    f"{label}: no age field configured in AGE_FIELD. Skipped."))
                continue

            model = self._model(label)
            cutoff = now - timezone.timedelta(days=days)
            n, oldest, newest = self._describe(model, field, cutoff)
            size_before = self._table_size(model)

            self.stdout.write(f"\n{label}")
            self.stdout.write(
                f"  window        : {days} days  (cutoff {cutoff:%Y-%m-%d %H:%M} UTC, "
                f"aged by {field})")
            self.stdout.write(f"  total rows    : {model.objects.count()}")
            self.stdout.write(f"  would delete  : {n}")
            self.stdout.write(f"  oldest/newest : {oldest} .. {newest}")
            self.stdout.write(f"  table size    : {size_before}")

            # Path is decided per model and PRINTED, so the raw-versus-ORM choice
            # is visible at run time and not an assumption from the docstring.
            fks = self._has_inbound_fks(model)
            sigs = self._has_delete_receivers(model)
            raw_ok = not fks and not sigs
            self.stdout.write(
                f"  path          : {'raw chunked SQL' if raw_ok else 'ORM .delete()'}"
                f"  (inbound FKs: {fks}, delete signals: {sigs})")

            if not commit or not n:
                continue

            removed = (self._delete_raw(model, field, cutoff) if raw_ok
                       else self._delete_orm(model, field, cutoff))
            total += removed
            self.stdout.write(self.style.SUCCESS(f"  DELETED       : {removed}"))
            self.stdout.write(f"  table size now: {self._table_size(model)} "
                              f"(was {size_before}; VACUUM reclaims the rest)")

        self.stdout.write("\n" + "=" * 72)
        if commit:
            self.stdout.write(self.style.SUCCESS(f"Deleted {total} row(s) in total."))
        else:
            self.stdout.write(self.style.WARNING(
                "Dry run. Nothing was deleted. Re-run with --commit to apply."))
        self._action_log_note()

    def _action_log_note(self):
        model = self._model("accounts.ActionLog")
        self.stdout.write(
            f"\naccounts.ActionLog is EXCLUDED by design and was not touched "
            f"({model.objects.count()} rows). Run --report-action-logs for a what-if."
        )

    def _report_action_logs(self, now):
        """What ActionLog pruning would remove. Deletes nothing, then exits."""
        model = self._model("accounts.ActionLog")
        field = AGE_FIELD["accounts.ActionLog"]
        total = model.objects.count()

        self.stdout.write("ACTION LOG RETENTION, WHAT-IF ONLY")
        self.stdout.write("=" * 72)
        self.stdout.write(
            "accounts.ActionLog is the audit trail. It is absent from\n"
            "settings.LOG_RETENTION_DAYS on purpose, so prune_logs never deletes\n"
            "from it. These numbers exist so a person can make that call on\n"
            "evidence; acting on one means adding the model to LOG_RETENTION_DAYS\n"
            "deliberately, which is a reviewable change.\n")
        self.stdout.write(f"  table       : {model._meta.db_table}")
        self.stdout.write(f"  total rows  : {total}")
        self.stdout.write(f"  size        : {self._table_size(model)}")
        self.stdout.write(f"  aged by     : {field}\n")

        for days in ACTION_LOG_WHATIF_WINDOWS:
            cutoff = now - timezone.timedelta(days=days)
            n, oldest, newest = self._describe(model, field, cutoff)
            pct = (n * 100 / total) if total else 0
            self.stdout.write(
                f"  {days:>4} days -> would delete {n:>8} of {total} ({pct:5.1f}%)"
                f"   oldest {oldest} .. newest {newest}")

        self.stdout.write(self.style.WARNING(
            "\nNothing was deleted. This flag never deletes."))
