# STATE-ONLY MIGRATION — never run via `python manage.py migrate`.
#
# The DDL lives in backend/sql/2026_08_bookings_created_order.sql and is
# hand-run per the project's schema-change convention; that file also inserts
# this migration's row into django_migrations so Django considers it applied.
# This file exists so model state matches the database and `makemigrations`
# stays clean. `python manage.py sync_indexes --apply` creates the same index
# from model state and is the safe way to catch a database this never reached.
#
# DEPLOY ORDER. Unlike 0013 this is index-only — no column, no backfill — so the
# Python is CORRECT without it: BookDelegateViewSet.ordering becomes
# ["-created_at", "-id"], and created_at is an existing NOT NULL column. Without
# the index the sort still returns the right rows, it just sorts the whole table
# to return 50. Run the SQL first anyway; there is no reason to serve the slow
# plan.
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run inside a transaction block. Declared even though
    # this file is never executed, so that the operation stays legal if anybody
    # ever does run it against an empty database.
    atomic = False

    dependencies = [
        ("book_delegate", "0013_booked_on"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="bookdelegate",
            index=models.Index(
                models.F("created_at").desc(),
                models.F("id").desc(),
                name="book_delegates_created_id_idx",
            ),
        ),
    ]
