from django.db import migrations


class Migration(migrations.Migration):
    """
    Drops payment_due_date from book_events.

    Nothing reads it any more; the Bookings screens never offered it, the
    importers only ever back-filled it from the Zoho export, and on 9 Sep 2026
    no open invoice carried a value. Same delivery shape as events 0019; the
    production DDL runs by hand from backend/sql/2026_09_payment_due_prune.sql,
    which records this migration as applied, and the guarded DDL here serves
    databases built from migrations alone.
    """

    dependencies = [
        ("book_event", "0026_alter_bookevent_payment_status"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="bookevent", name="payment_due_date"),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=["ALTER TABLE book_events DROP COLUMN IF EXISTS payment_due_date;"],
                    reverse_sql=["ALTER TABLE book_events ADD COLUMN IF NOT EXISTS payment_due_date DATE NULL;"],
                ),
            ],
        ),
    ]
