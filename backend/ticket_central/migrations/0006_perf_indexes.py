# STATE-ONLY MIGRATION — never run via `python manage.py migrate`.
#
# The DDL lives in backend/sql/2026_08_perf_indexes.sql and is hand-run per the
# project's schema-change convention; that file also inserts this migration's
# row into django_migrations so Django considers it applied. This file exists
# so model state matches the database and `makemigrations` stays clean.
#
# Expression indexes (the COALESCE one) cannot be expressed in Meta and are
# deliberately absent here; they exist only in the SQL file and in
# sync_indexes' pg_index-based check from this workstream onward.
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run inside a transaction block. Declared even though
    # this file is never executed, so that the operation stays legal if anybody
    # ever does run it against an empty database.
    atomic = False

    dependencies = [
        ("ticket_central", "0005_remove_dmd_in_progress"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="ticket",
            index=models.Index(
                fields=["-created_at", "-id"], name="tickets_created_id_idx"
            ),
        ),
        AddIndexConcurrently(
            model_name="ticket",
            index=models.Index(
                fields=["status", "-created_at", "-id"],
                name="tickets_status_created_id_idx",
            ),
        ),
    ]
