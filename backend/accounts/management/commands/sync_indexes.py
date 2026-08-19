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
    python manage.py sync_indexes --strict    # exit 1 if the DB has drifted, for CI
"""
import sys

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
        parser.add_argument(
            "--strict", action="store_true",
            help="Exit nonzero if the DATABASE INDEX DRIFT section finds anything. "
                 "For CI, where a silent all-clear is the failure mode this "
                 "command exists to prevent.",
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

    # ── Database truth, as opposed to migration state ─────────────────────────

    def _column_indexes_actual(self):
        """
        {table: {first_column, ...}} straight from pg_index.

        Meta.indexes is half the story: db_index=True and every ForeignKey also
        create indexes, and Django compares models against its own MIGRATION
        STATE rather than the database, so a --fake or a restore from an older
        dump lets the two drift with a false all-clear. Skipped silently on
        non-PostgreSQL engines (local SQLite dev), where pg_index does not exist.

        FIRST COLUMN ONLY, deliberately. A composite index leading with a column
        also serves a lookup on that column alone, so first-column coverage is
        the right question for "is this field indexed at all". It is a coverage
        check, not an equality check: it answers "can the planner use anything
        here", never "is this the ideal index".

        ON THE SUBSCRIPT. indkey is an int2vector, and casting it to int2[]
        yields an array whose lower bound is ZERO, not one. (i.indkey::int2[])[0]
        is therefore the first indexed column and [1] is the second. Verified
        against this database rather than assumed: book_events_pkey is a
        single-column index on id and has indkey = '1', for which [0] returns 1
        (id's attnum) and [1] returns NULL. Using [1] here would silently report
        every single-column index as covering nothing, which reads as total
        drift rather than as a bug in this query.

        attnum > 0 excludes system columns; a 0 in indkey marks an expression
        rather than a plain column, and joining on pg_attribute drops those
        rows, which is correct — an expression index on lower(email) does not
        cover a plain email lookup.
        """
        if connection.vendor != "postgresql":
            return None
        actual = {}
        with connection.cursor() as cur:
            cur.execute("""
                SELECT t.relname, a.attname
                FROM pg_index i
                JOIN pg_class     t ON t.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                JOIN pg_attribute a ON a.attrelid = t.oid
                                   AND a.attnum  = (i.indkey::int2[])[0]
                WHERE n.nspname = 'public'
                  AND a.attnum > 0
            """)
            for table, column in cur.fetchall():
                actual.setdefault(table, set()).add(column)
        return actual

    def _column_indexes_expected(self):
        """
        [(table, column, model, field)] for every field the MODELS say is indexed.

        That is db_index=True and every ForeignKey, since Django indexes an FK
        automatically unless told db_index=False. Primary keys are excluded:
        they are covered by the pk constraint and can never be missing.

        Uses field.column rather than field.name, because the two differ exactly
        where it matters most — BookDelegate.invoice is declared with
        db_column="invoice_number", so the column to look for is invoice_number
        and not the invoice_id this would otherwise guess.
        """
        expected = []
        for model in apps.get_models():
            meta = model._meta
            if not meta.managed or meta.proxy:
                continue
            for field in meta.local_fields:
                if field.primary_key:
                    continue
                if getattr(field, "db_index", False) or field.many_to_one:
                    expected.append((meta.db_table, field.column, model, field))
        return expected

    def _column_drift(self):
        """
        [(table, column, model, field)] the models index and the database does not.

        None when the check cannot run at all (non-PostgreSQL), which the caller
        must distinguish from the empty list, "ran and found nothing".
        """
        actual = self._column_indexes_actual()
        if actual is None:
            return None
        return [
            row for row in self._column_indexes_expected()
            if row[1] not in actual.get(row[0], set())
        ]

    def _report_column_drift(self, drift):
        """Print the DATABASE INDEX DRIFT section. Returns nothing."""
        # ASCII only in everything this command PRINTS. The Windows console this
        # is run from is cp1252, and box-drawing characters raise
        # UnicodeEncodeError there — which would make the drift report crash on
        # the one machine most likely to need it. Docstrings above may use them
        # freely; they are never written to stdout.
        self.stdout.write("")
        self.stdout.write("-- DATABASE INDEX DRIFT " + "-" * 40)

        if drift is None:
            self.stdout.write(
                "  Skipped: this check reads pg_index and the active database is "
                f"{connection.vendor}, not postgresql."
            )
            return

        if not drift:
            self.stdout.write(self.style.SUCCESS(
                "  Every db_index=True field and every ForeignKey has an index in "
                "the database."
            ))
            return

        self.stdout.write(self.style.WARNING(
            f"  {len(drift)} column(s) are indexed in the MODELS and not in the "
            f"DATABASE.\n"
            f"  Django reports no missing migrations for these, because it "
            f"compares models\n"
            f"  against its own migration state and never against the schema.\n"
        ))
        for table, column, model, field in sorted(drift):
            kind = "FK" if field.many_to_one else "db_index"
            self.stdout.write(
                f"    {table:<26} {column:<26} ({kind}) "
                f"{model.__name__}.{field.name}"
            )
        self.stdout.write(self.style.WARNING(
            "\n  These are NOT created by --apply: this command only creates named "
            "Meta.indexes\n  entries. Auto-named indexes are fixed by "
            "backend/sql/2026_08_perf_indexes.sql."
        ))

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
        # The DRIFT check is computed before anything else and reported after
        # everything else, so it prints on every exit path. The Meta-index work
        # below returns early when there is nothing to do, and the drift section
        # is the half of the report most likely to be non-empty on a database
        # that has been restored or --fake'd — losing it to an early return
        # would reproduce exactly the false all-clear this command exists to end.
        drift = self._column_drift()

        self._sync_meta_indexes(options)

        self._report_column_drift(drift)

        if options["strict"] and drift:
            self.stderr.write(self.style.ERROR(
                f"\n--strict: {len(drift)} column(s) drifted. Exiting 1."
            ))
            sys.exit(1)

    def _sync_meta_indexes(self, options):
        """The named-Meta.indexes reconciliation. May return early."""
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
