# STATE-ONLY MIGRATION — never run via `python manage.py migrate`.
#
# The DDL lives in backend/sql/2026_08_ticket_trgm.sql and is hand-run per the
# project's schema-change convention; that file also inserts this migration's
# row into django_migrations so Django considers it applied. This file exists
# so model state matches the database and `makemigrations` stays clean.
#
# TrigramExtension is declared first because every index below depends on the
# gin_trgm_ops operator class, which does not exist until pg_trgm is installed.
# Installing an extension needs database-owner rights, which is the other reason
# this is hand-run rather than left to an application-role migrate.
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.contrib.postgres.operations import AddIndexConcurrently, TrigramExtension
from django.db import migrations
from django.db.models.functions import Upper


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run inside a transaction block.
    atomic = False

    dependencies = [
        ("ticket_central", "0006_perf_indexes"),
    ]

    operations = [
        TrigramExtension(),
        AddIndexConcurrently(
            model_name="ticket",
            index=GinIndex(OpClass(Upper("ticket_number"), name="gin_trgm_ops"),
                           name="tickets_ticketnum_trgm_idx"),
        ),
        AddIndexConcurrently(
            model_name="ticket",
            index=GinIndex(OpClass(Upper("event_code"), name="gin_trgm_ops"),
                           name="tickets_event_code_trgm_idx"),
        ),
        AddIndexConcurrently(
            model_name="ticket",
            index=GinIndex(OpClass(Upper("purpose"), name="gin_trgm_ops"),
                           name="tickets_purpose_trgm_idx"),
        ),
        AddIndexConcurrently(
            model_name="ticket",
            index=GinIndex(OpClass(Upper("organizer"), name="gin_trgm_ops"),
                           name="tickets_organizer_trgm_idx"),
        ),
        AddIndexConcurrently(
            model_name="ticket",
            index=GinIndex(OpClass(Upper("competitor_event_name"), name="gin_trgm_ops"),
                           name="tickets_competitor_trgm_idx"),
        ),
        AddIndexConcurrently(
            model_name="ticket",
            index=GinIndex(OpClass(Upper("assigned_mr"), name="gin_trgm_ops"),
                           name="tickets_assigned_mr_trgm_idx"),
        ),
        AddIndexConcurrently(
            model_name="ticket",
            index=GinIndex(OpClass(Upper("assign_name"), name="gin_trgm_ops"),
                           name="tickets_assign_name_trgm_idx"),
        ),
    ]
