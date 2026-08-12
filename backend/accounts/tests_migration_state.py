"""
accounts/tests_migration_state.py
──────────────────────────────────
Two migration guards, both prompted by a real outage.

WHAT HAPPENED
`paper_review` landed with models, a viewset and a URL registration, but its
0001_initial was never applied to the working database. Because
ProposalSubmission carries an FK to PaperReview, the missing table took BOTH
endpoints down together:

    /api/paper-reviews/          500  relation "paper_reviews" does not exist
    /api/proposal-submissions/   500  relation "paper_reviews" does not exist

The second one had been returning 200 earlier the same day, so a module nobody
had touched broke because of a sibling's unapplied migration. Nothing failed
until a request arrived — the test suite was green throughout, because Django
builds a FRESH test database from the migration files and therefore always has
every table.

WHAT THESE GUARD
1. test_no_missing_migrations — model changes with no accompanying migration file.
   That is the state that produces "no such column" in production while the suite
   passes. `makemigrations --check` is the standard detector; running it here means
   CI catches it without anyone remembering to run it by hand.

2. test_no_unapplied_migrations_on_the_configured_database — migration files that
   exist but have not been applied to the database this settings module points at.
   Skipped when that database is unreachable, so a checkout with no local Postgres
   still runs the rest of the suite.

Guard 1 is the CI gate. Guard 2 is the one that would have caught the actual
outage, and it only means anything when run against a real environment.
"""
import io

from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase


class MigrationStateTests(TestCase):

    def test_no_missing_migrations(self):
        """
        Every model change must have a migration file.

        `makemigrations --check --dry-run` exits non-zero when it would have had to
        write one. call_command surfaces that as SystemExit.
        """
        out = io.StringIO()
        try:
            call_command("makemigrations", "--check", "--dry-run",
                         stdout=out, stderr=out, verbosity=1)
        except SystemExit:
            self.fail(
                "Model changes exist with no migration file. Run:\n"
                "    python manage.py makemigrations\n\n"
                "Django reported:\n" + out.getvalue()
            )

    def test_no_unapplied_migrations_on_the_configured_database(self):
        """
        Migration files that exist but are not applied to the database in settings.

        This is the check that would have caught the paper_review outage. It reads
        the DEFAULT alias directly rather than the test database, because the test
        database is rebuilt from the migration files every run and so is fully
        migrated by construction — it can never show the problem.
        """
        try:
            conn = connections[DEFAULT_DB_ALIAS]
            conn.ensure_connection()
        except Exception as exc:                                  # noqa: BLE001
            self.skipTest(f"configured database not reachable: {exc}")

        executor = MigrationExecutor(conn)
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)

        if plan:
            pending = ", ".join(f"{m.app_label}.{m.name}" for m, _backwards in plan)
            self.fail(
                f"{len(plan)} migration(s) exist but are NOT applied to "
                f"'{conn.settings_dict['NAME']}'. Requests touching these tables will "
                f"500 while this suite stays green, because the test database is built "
                f"from the migration files.\n\n"
                f"Run:  python manage.py migrate\n\nPending: {pending}"
            )
