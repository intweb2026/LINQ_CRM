"""
Wipe operational CRM data so a fresh import can be loaded.

The in-app "Clear All" buttons (Events / Bookings / Ticket Central) only empty
six tables between them — companies and webhook_events, the two largest
datasets, survive. This command clears everything operational in one pass and
leaves the accounts layer (users, roles, permissions, teams, API tokens)
untouched so nobody has to be re-created.

    python manage.py wipe_data                       # dry run — shows the plan
    python manage.py wipe_data --confirm WIPE        # actually delete
    python manage.py wipe_data --confirm WIPE --include-accounts

Always take a fresh pg_dump first; TRUNCATE is not recoverable.
"""
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

# Accounts layer — who can log in and what they may do.
ACCOUNT_MODELS = {
    "accounts.User",
    "accounts.UserPermission",
    "teams.Team",
    "teams.TeamPermission",
    "authtoken.Token",
}

# Django's own plumbing. Never truncated: dropping content types or migration
# history breaks the install rather than clearing it.
FRAMEWORK_MODELS = {
    "auth.Permission",
    "auth.Group",
    "contenttypes.ContentType",
    "sessions.Session",
    "admin.LogEntry",
}

CONFIRM_TOKEN = "WIPE"


class Command(BaseCommand):
    help = "Delete all operational CRM data (events, bookings, tickets, companies, logs)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            default="",
            help=f"Pass --confirm {CONFIRM_TOKEN} to actually delete. Without it this is a dry run.",
        )
        parser.add_argument(
            "--include-accounts",
            action="store_true",
            help="Also wipe users, roles, teams and API tokens. You will need a superuser afterwards.",
        )
        parser.add_argument(
            "--keep-companies",
            action="store_true",
            help="Preserve the companies table (useful when only re-importing events/bookings).",
        )

    # ── planning ─────────────────────────────────────────────────────────────
    def _keep(self, label, include_accounts, keep_companies):
        if label in FRAMEWORK_MODELS:
            return True
        if label in ACCOUNT_MODELS:
            return not include_accounts
        return keep_companies and label == "companies.Company"

    def _plan(self, include_accounts, keep_companies):
        preserved, targeted = [], []
        for model in apps.get_models():
            # Proxy models share another model's table (authtoken.TokenProxy sits
            # on authtoken_token); counting them once avoids classifying the same
            # table as both preserved and targeted.
            if model._meta.proxy:
                continue
            label = model._meta.label
            try:
                count = model.objects.count()
            except Exception:                                    # noqa: BLE001
                continue
            row = (label, model._meta.db_table, count)
            if self._keep(label, include_accounts, keep_companies):
                preserved.append(row)
            else:
                targeted.append(row)

        # Belt and braces: never truncate a table any preserved model reads.
        safe = {t for _, t, _ in preserved}
        targeted = [r for r in targeted if r[1] not in safe]
        return sorted(preserved), sorted(targeted)

    def _all_table_counts(self):
        """Row counts for every table in the schema, to expose CASCADE effects."""
        with connection.cursor() as cur:
            cur.execute(
                "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY relname;"
            )
            tables = [r[0] for r in cur.fetchall()]
            counts = {}
            for t in tables:
                cur.execute(f"SELECT count(*) FROM {connection.ops.quote_name(t)};")
                counts[t] = cur.fetchone()[0]
        return counts

    def handle(self, *args, **options):
        confirmed = options["confirm"].strip().upper() == CONFIRM_TOKEN
        preserved, targeted = self._plan(options["include_accounts"], options["keep_companies"])

        self.stdout.write(self.style.MIGRATE_HEADING("\nWill DELETE:"))
        total = 0
        for label, table, count in targeted:
            total += count
            style = self.style.WARNING if count else (lambda s: s)
            self.stdout.write(f"  {label:<44}{table:<32}{style(f'{count:>10,}')}")

        self.stdout.write(self.style.MIGRATE_HEADING("\nWill PRESERVE:"))
        for label, table, count in preserved:
            self.stdout.write(f"  {label:<44}{table:<32}{count:>10,}")

        self.stdout.write(f"\n{total:,} rows across {len(targeted)} tables would be deleted.")

        if not confirmed:
            self.stdout.write(self.style.NOTICE(
                f"\nDry run — nothing changed. Re-run with --confirm {CONFIRM_TOKEN} to proceed.\n"
            ))
            return

        tables = [t for _, t, _ in targeted]
        if not tables:
            raise CommandError("Nothing to truncate.")

        # One statement so FK order does not matter. CASCADE also empties join
        # tables pointing at these (users_assigned_events, for one); snapshot
        # everything so those knock-on effects are reported rather than silent.
        quoted = ", ".join(connection.ops.quote_name(t) for t in tables)
        before_all = self._all_table_counts()

        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE;")

            damaged = []
            for label, table, before in preserved:
                after = apps.get_model(label).objects.count()
                if after != before:
                    damaged.append(f"{label} ({table}): {before:,} -> {after:,}")
            if damaged:
                raise CommandError(
                    "CASCADE reached preserved tables, rolling back:\n  " + "\n  ".join(damaged)
                )

            after_all = self._all_table_counts()

        cascaded = [
            (t, before_all[t]) for t in sorted(before_all)
            if t not in set(tables) and before_all[t] and not after_all.get(t)
        ]
        if cascaded:
            self.stdout.write(self.style.MIGRATE_HEADING("\nAlso emptied via CASCADE:"))
            for t, n in cascaded:
                self.stdout.write(f"  {t:<44}{n:>10,}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDeleted {total:,} rows from {len(tables)} tables. Identity sequences reset."
        ))
        if options["include_accounts"]:
            self.stdout.write(self.style.WARNING(
                "Accounts were wiped — run `python manage.py createsuperuser` before logging in."
            ))
