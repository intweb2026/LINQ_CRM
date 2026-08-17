"""
accounts/management/commands/sync_indexes.py
─────────────────────────────────────────────
Create the indexes the models DECLARE but the database does not HAVE.

THE PROBLEM THIS EXISTS FOR
`makemigrations --check` reports no changes and `showmigrations` shows everything
applied, and both are telling the truth: Django compares the models against its
own recorded migration STATE, never against the database. When a migration is
recorded as applied without its DDL actually running — a `--fake`, a
`--fake-initial` over pre-existing tables, or a database restored from a dump
taken before the migration existed — the recorded state and the real schema drift
apart, and nothing in Django's own toolchain will ever mention it again.

Measured on the development database on 2026-08-15, that drift was 36 indexes.
The worst were on the biggest tables in the CRM:

    webhook_events   130,304 rows   4 missing, including invoice_number
    book_delegates     1,251 rows   6 missing
    book_events          981 rows   9 missing
    events               217 rows   3 missing, including event_code

An index that exists in the model and not in the database is the most expensive
kind of missing index, because every reviewer reads the model, sees the index, and
concludes the lookup is covered.

WHAT IT DOES NOT DO
It never drops anything, never alters an existing index, and never touches
constraints or unique_together. It only creates named entries from a model's
Meta.indexes that are absent, so running it on a correct database is a no-op.

CONCURRENTLY BY DEFAULT
A plain CREATE INDEX takes an ACCESS EXCLUSIVE lock, which blocks reads and writes
on that table for the duration. That is imperceptible on 217 events and is not
something to do casually to a 130,000-row table an integration is posting into.
CONCURRENTLY takes no such lock; it costs a second table pass and cannot run
inside a transaction, which is why this command manages its own autocommit rather
than using the default atomic wrapper.

    python manage.py sync_indexes             # report only, changes nothing
    python manage.py sync_indexes --apply     # create them, CONCURRENTLY
    python manage.py sync_indexes --apply --no-concurrently
"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Create indexes declared in model Meta.indexes that are missing from the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually create the indexes. Without this the command only reports.",
        )
        parser.add_argument(
            "--no-concurrently", action="store_true",
            help="Use a plain CREATE INDEX, which locks the table. Faster, and fine "
                 "on a small table or a database nothing is reading.",
        )

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _existing_indexes(self):
        """{table: {index names}} for the public schema."""
        out = {}
        with connection.cursor() as cur:
            cur.execute(
                "SELECT tablename, indexname FROM pg_indexes WHERE schemaname = 'public'"
            )
            for table, name in cur.fetchall():
                out.setdefault(table, set()).add(name)
        return out

    def _invalid_indexes(self):
        """
        Names of indexes Postgres considers INVALID.

        A CREATE INDEX CONCURRENTLY that fails part-way leaves the index behind in
        this state: it is present in pg_indexes, so this command would consider it
        done, but the planner refuses to use it. Reported so a half-finished run
        is visible rather than looking like success.
        """
        with connection.cursor() as cur:
            cur.execute(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE NOT i.indisvalid"
            )
            return {row[0] for row in cur.fetchall()}

    def _missing(self, existing):
        """[(model, index)] for every named Meta index absent from its table."""
        found = []
        for model in apps.get_models():
            meta = model._meta
            if not meta.managed or meta.proxy:
                continue
            have = existing.get(meta.db_table, set())
            for index in meta.indexes:
                # An unnamed index cannot be matched by name, and Django only
                # leaves one unnamed if the developer did; there are none today.
                if index.name and index.name not in have:
                    found.append((model, index))
        return found

    # ── Entry point ───────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        concurrently = not options["no_concurrently"]

        invalid = self._invalid_indexes()
        if invalid:
            self.stderr.write(self.style.WARNING(
                f"{len(invalid)} index(es) exist but are INVALID, left behind by a "
                f"failed concurrent build. Postgres will not use them. Drop and "
                f"recreate: {', '.join(sorted(invalid))}"
            ))

        missing = self._missing(self._existing_indexes())
        if not missing:
            self.stdout.write(self.style.SUCCESS(
                "Every declared index is present. Nothing to do."
            ))
            return

        # Row counts, so the report says which of these actually matter.
        with connection.cursor() as cur:
            cur.execute("SELECT relname, n_live_tup FROM pg_stat_user_tables")
            live = dict(cur.fetchall())

        self.stdout.write(
            f"{len(missing)} declared index(es) are missing from the database:\n"
        )
        for model, index in missing:
            table = model._meta.db_table
            self.stdout.write(
                f"  {table:<26} {live.get(table, 0):>8} rows   {index.name}"
            )

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                "\nReport only. Re-run with --apply to create them."
            ))
            return

        created, failed = 0, 0
        with connection.schema_editor(atomic=False) as editor:
            for model, index in missing:
                sql = str(index.create_sql(model, editor))
                if concurrently:
                    # Prefix substitution rather than a hand-built statement, so the
                    # column list, operator classes, INCLUDE and WHERE clauses stay
                    # exactly what Django would have emitted. The prefix is fixed by
                    # Django's own sql_create_index templates.
                    if sql.startswith("CREATE UNIQUE INDEX "):
                        sql = sql.replace(
                            "CREATE UNIQUE INDEX ",
                            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ", 1)
                    elif sql.startswith("CREATE INDEX "):
                        sql = sql.replace(
                            "CREATE INDEX ",
                            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ", 1)
                try:
                    if concurrently:
                        # CONCURRENTLY is rejected inside a transaction block, and
                        # the connection may already be in one.
                        connection.set_autocommit(True)
                        with connection.cursor() as cur:
                            cur.execute(sql)
                    else:
                        editor.execute(sql)
                    created += 1
                    self.stdout.write(self.style.SUCCESS(f"  created {index.name}"))
                except Exception as exc:                       # noqa: BLE001
                    # One index that cannot be built must not abandon the rest —
                    # they are independent, and a partial reconciliation is still
                    # strictly better than none.
                    failed += 1
                    self.stderr.write(self.style.ERROR(
                        f"  FAILED  {index.name} on {model._meta.db_table}: {exc}"
                    ))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Created {created} index(es)."))
        if failed:
            self.stderr.write(self.style.ERROR(
                f"{failed} failed; see above. Re-running is safe, this command only "
                f"creates what is still absent."
            ))
