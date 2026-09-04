from django.db import migrations


class Migration(migrations.Migration):
    """
    Drops capacity and expected_revenue from events.

    Neither was ever exposed by the Events API: the screen mocked capacity as 0
    and only the retired Event Performance module read it. Both default to a
    constant on every row, so nothing is lost. Same delivery shape as 0018: the
    production DDL runs by hand from backend/sql/2026_09_events_prune.sql, which
    records this migration as applied; the guarded DDL here serves databases
    built from migrations alone.
    """

    dependencies = [
        ("events", "0018_base_code_year_verdict"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="event", name="capacity"),
                migrations.RemoveField(model_name="event", name="expected_revenue"),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=[
                        "ALTER TABLE events DROP COLUMN IF EXISTS capacity;",
                        "ALTER TABLE events DROP COLUMN IF EXISTS expected_revenue;",
                    ],
                    reverse_sql=[
                        "ALTER TABLE events ADD COLUMN IF NOT EXISTS capacity INTEGER NOT NULL DEFAULT 500;",
                        "ALTER TABLE events ADD COLUMN IF NOT EXISTS expected_revenue NUMERIC(14,2) NOT NULL DEFAULT 0;",
                    ],
                ),
            ],
        ),
    ]
