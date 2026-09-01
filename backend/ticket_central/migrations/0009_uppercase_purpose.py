"""
Fold existing purposes to the upper-case storage form.

New writes are normalised by Ticket.save() and utils._coerce_row, but the rows
already in the table came from Zoho and from webhooks that push lower case, so
production holds "Pharma General" beside otherwise all-upper codes. Left alone
they would keep their own TicketSequence rows, which is the split-counter bug
the normalisation exists to stop.

ticket_sequences is folded the same way. purpose_key is unique, so duplicates
that collapse onto one key are merged by keeping the highest last_number; the
counter must never move backwards.
"""
from django.db import migrations

# btrim + collapse runs of whitespace + upper, matching utils.normalize_purpose.
_NORMALISED = r"upper(regexp_replace(btrim(purpose), '\s+', ' ', 'g'))"


def forwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        cur.execute(
            f"UPDATE tickets SET purpose = {_NORMALISED} "
            f"WHERE purpose <> {_NORMALISED};"
        )

    TicketSequence = apps.get_model("ticket_central", "TicketSequence")
    highest = {}
    for seq in TicketSequence.objects.all():
        key = " ".join(seq.purpose_key.split()).upper()[:50]
        if key not in highest or seq.last_number > highest[key].last_number:
            highest[key] = seq

    keep_ids = {s.id for s in highest.values()}
    TicketSequence.objects.exclude(id__in=keep_ids).delete()
    for key, seq in highest.items():
        if seq.purpose_key != key:
            seq.purpose_key = key
            seq.save(update_fields=["purpose_key"])


def backwards(apps, schema_editor):
    """Case is not recoverable once folded, and the merged rows are gone."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("ticket_central", "0008_backfill_added_user_text"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
