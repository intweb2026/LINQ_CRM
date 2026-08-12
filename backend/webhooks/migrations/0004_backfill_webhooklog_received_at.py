"""
Backfill WebhookLog.received_at from created_at.

The Delivery logs table sorts newest-first on `received_at`, and Postgres puts
NULLs FIRST under DESC — so ten rows with no received_at sat permanently at the
top of page one and pushed genuinely new deliveries below the fold. The two
columns are written microseconds apart in the same request, so created_at is an
accurate stand-in wherever received_at was never set.

The ordering itself has also been moved onto created_at (non-null at the DB
level), so a future NULL cannot reintroduce this. This migration cleans up the
rows that already exist.
"""
from django.db import migrations
from django.db.models import F


def backfill_received_at(apps, schema_editor):
    WebhookLog = apps.get_model("webhooks", "WebhookLog")
    WebhookLog.objects.filter(received_at__isnull=True).update(received_at=F("created_at"))


def noop(apps, schema_editor):
    """Irreversible by design — the original NULLs carried no information."""


class Migration(migrations.Migration):

    dependencies = [
        ("webhooks", "0003_alter_webhooklog_db_insert_status_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_received_at, noop),
    ]
