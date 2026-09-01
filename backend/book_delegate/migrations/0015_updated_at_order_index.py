# STATE-ONLY MIGRATION — never run via `python manage.py migrate`.
#
# The DDL lives in backend/sql/2026_08_bookings_modified_order.sql and is
# hand-run per the project's schema-change convention; that file also inserts
# this migration's row into django_migrations so Django considers it applied.
# This file exists so model state matches the database and `makemigrations`
# stays clean. `python manage.py sync_indexes --apply` creates the same index
# from model state and is the safe way to catch a database this never reached.
#
# DEPLOY ORDER. Index-only, like 0014 — no column, no backfill — so the Python is
# CORRECT without it: BookDelegateViewSet.ordering becomes ["-updated_at", "-id"]
# and updated_at is an existing NOT NULL column (auto_now=True). Without the index
# the sort still returns the right rows, it just sorts all ~14,800 delegates to
# return 50. Run the SQL first anyway; there is no reason to serve the slow plan.
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run inside a transaction block. Declared even though
    # this file is never executed, so that the operation stays legal if anybody
    # ever does run it against an empty database.
    atomic = False

    dependencies = [
        ("book_delegate", "0014_created_at_order_index"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="bookdelegate",
            index=models.Index(
                models.F("updated_at").desc(),
                models.F("id").desc(),
                name="book_delegates_updated_id_idx",
            ),
        ),
    ]
