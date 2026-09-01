"""
Repeated-link detection, and the column it replaces.

  · drops `duplicate_tickets`, the hand-typed Zoho column. 873 rows carried a
    value, 572 of them the literal "-" and most of the rest the "⚠️" marker with
    an earlier ticket number. Repeats are computed live now, so the column could
    only drift out of agreement with the check. THIS DISCARDS THAT TEXT; reverse
    puts the column back, empty.
  · adds `link_key`, a sha1 of the normalised link, and backfills it for every
    existing row so a repeat is caught against history and not only against
    today's entries.
  · adds an index on Upper(added_user_text), now that the list is scoped by it.
  · flips Meta.ordering to ascending. No index changes: PostgreSQL scans the
    existing descending composites backwards.
"""
from django.db import migrations, models
from django.db.models.functions import Upper


def backfill_link_key(apps, schema_editor):
    """
    Fill link_key for existing rows, in batches.

    The digest is computed in Python by ticket_central.utils.link_digest, the
    same function the model's save() uses — importing it rather than restating
    the normalisation is the point, since a second copy could drift and the
    check would then silently miss older rows. utils imports the real model,
    which is fine here: only the pure string helpers are used.
    """
    from ticket_central.utils import link_digest

    Ticket = apps.get_model("ticket_central", "Ticket")
    qs = Ticket.objects.exclude(link_url="").only("id", "link_url")
    batch, size = [], 2000
    for row in qs.iterator(chunk_size=size):
        row.link_key = link_digest(row.link_url)
        batch.append(row)
        if len(batch) >= size:
            Ticket.objects.bulk_update(batch, ["link_key"])
            batch = []
    if batch:
        Ticket.objects.bulk_update(batch, ["link_key"])


def clear_link_key(apps, schema_editor):
    Ticket = apps.get_model("ticket_central", "Ticket")
    Ticket.objects.exclude(link_key="").update(link_key="")


class Migration(migrations.Migration):

    dependencies = [
        ("ticket_central", "0009_uppercase_purpose"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="link_key",
            field=models.CharField(blank=True, db_index=True, default="",
                                   max_length=40),
        ),
        migrations.RunPython(backfill_link_key, clear_link_key),
        migrations.RemoveField(
            model_name="ticket",
            name="duplicate_tickets",
        ),
        migrations.AddIndex(
            model_name="ticket",
            index=models.Index(Upper("added_user_text"),
                               name="tickets_added_user_idx"),
        ),
        migrations.AlterModelOptions(
            name="ticket",
            options={"ordering": ["created_at", "id"]},
        ),
    ]
