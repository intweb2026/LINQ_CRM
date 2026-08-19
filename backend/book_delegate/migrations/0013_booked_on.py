# STATE-ONLY MIGRATION — never run via `python manage.py migrate`.
#
# The DDL and the backfill live in backend/sql/2026_08_booked_on.sql and are
# hand-run per the project's schema-change convention; that file also inserts
# this migration's row into django_migrations so Django considers it applied.
# This file exists so model state matches the database and `makemigrations`
# stays clean.
#
# DEPLOY ORDER IS LOAD-BEARING. The SQL file must be run BEFORE the Python from
# this workstream is deployed. If the code ships first, booked_on is NULL on
# every row, the new default ordering collapses to the pk tiebreak alone, and
# the Bookings table appears to reorder itself for every user.
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run inside a transaction block. Declared even though
    # this file is never executed, so that the operation stays legal if anybody
    # ever does run it against an empty database.
    atomic = False

    dependencies = [
        ("book_delegate", "0012_bookdelegate_booking_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookdelegate",
            name="booked_on",
            field=models.DateField(blank=True, editable=False, null=True),
        ),
        AddIndexConcurrently(
            model_name="bookdelegate",
            index=models.Index(
                models.F("booked_on").desc(nulls_last=True),
                models.F("id").desc(),
                name="book_delegates_booked_id_idx",
            ),
        ),
    ]
