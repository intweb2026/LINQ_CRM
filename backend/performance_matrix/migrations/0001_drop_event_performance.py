from django.db import migrations


class Migration(migrations.Migration):
    """
    Removes what the Event Performance module left behind.

    The three tables held follow-ups, mailshots and notes keyed by event code.
    All three were EMPTY in the live data (0 rows each on 2026-09-04), and the
    module's code is deleted, so the tables would only ever be orphans. The
    django_migrations rows go with them, or `migrate` would keep reporting an
    app it can no longer find.

    Same delivery shape as events/0018: backend/sql/2026_09_performance_matrix.sql
    runs this by hand on production and records this migration as applied.
    """

    initial = True

    dependencies = [
        ("events", "0018_base_code_year_verdict"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "DROP TABLE IF EXISTS ep_follow_ups;",
                "DROP TABLE IF EXISTS ep_mailshots;",
                "DROP TABLE IF EXISTS ep_notes;",
                "DELETE FROM django_migrations WHERE app = 'event_performance';",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
