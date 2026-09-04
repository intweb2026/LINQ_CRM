from django.db import migrations, models


def backfill(apps, schema_editor):
    """
    year from event_date, base_code from the internal code through
    events.codes.derive_base_code: the same default Event.save() applies from
    now on, so rows written before and after this migration agree. Only blanks
    are touched. backend/sql/2026_09_performance_matrix.sql does the same in
    SQL for the hand-run production path.
    """
    from events.codes import derive_base_code

    Event = apps.get_model("events", "Event")
    for pk, code, event_date, year, base in Event.objects.values_list(
        "pk", "event_code", "event_date", "year", "base_code"
    ).iterator():
        fix = {}
        if not year and event_date:
            fix["year"] = event_date.year
        if not (base or "").strip():
            fix["base_code"] = derive_base_code(code)
        if fix:
            Event.objects.filter(pk=pk).update(**fix)


class Migration(migrations.Migration):
    """
    Edition identity for the Performance Matrix: base_code, year, verdict.

    master_code -> base_code is a RENAME, not a drop and add. The column was
    empty on every row in the live data (0 of 217) so nothing is at stake, and a
    rename keeps whatever a future Events.csv load might put there.

    Same shape as accounts/0028: the production DDL is run by hand from
    backend/sql/2026_09_performance_matrix.sql, which also INSERTs this row into
    django_migrations, and the DDL is mirrored here, guarded, so a database built
    from migrations alone (every test run) gets the columns too and running both
    in either order is harmless.
    """

    dependencies = [
        ("events", "0017_remove_event_speaker_sales_team"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameField("event", "master_code", "base_code"),
                migrations.AddField(
                    model_name="event",
                    name="year",
                    field=models.PositiveIntegerField(blank=True, db_index=True, null=True),
                ),
                migrations.AddField(
                    model_name="event",
                    name="verdict",
                    field=models.CharField(
                        blank=True, default="", max_length=30,
                        choices=[
                            ("Standby", "Standby"), ("Going Ahead", "Going Ahead"),
                            ("Needs a push", "Needs a push"), ("Full Efforts Req.", "Full Efforts Req."),
                            ("Postponed", "Postponed"), ("TBP", "TBP"), ("Cancelled", "Cancelled"),
                        ],
                    ),
                ),
                migrations.AddIndex(
                    model_name="event",
                    index=models.Index(fields=["base_code", "year"], name="events_base_year_idx"),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=[
                        # Rename only when the old column is still there and the
                        # new one is not, so a re-run is a no-op.
                        "DO $$ BEGIN "
                        "IF EXISTS (SELECT 1 FROM information_schema.columns "
                        "           WHERE table_name = 'events' AND column_name = 'master_code') "
                        "AND NOT EXISTS (SELECT 1 FROM information_schema.columns "
                        "           WHERE table_name = 'events' AND column_name = 'base_code') THEN "
                        "  ALTER TABLE events RENAME COLUMN master_code TO base_code; "
                        "END IF; END $$;",
                        "ALTER TABLE events ADD COLUMN IF NOT EXISTS year INTEGER NULL;",
                        "ALTER TABLE events ADD COLUMN IF NOT EXISTS verdict VARCHAR(30) NOT NULL DEFAULT '';",
                        "CREATE INDEX IF NOT EXISTS events_year_idx ON events (year);",
                        "CREATE INDEX IF NOT EXISTS events_base_year_idx ON events (base_code, year);",
                    ],
                    reverse_sql=[
                        "DROP INDEX IF EXISTS events_base_year_idx;",
                        "DROP INDEX IF EXISTS events_year_idx;",
                        "ALTER TABLE events DROP COLUMN IF EXISTS verdict;",
                        "ALTER TABLE events DROP COLUMN IF EXISTS year;",
                        "ALTER TABLE events RENAME COLUMN base_code TO master_code;",
                    ],
                ),
            ],
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
