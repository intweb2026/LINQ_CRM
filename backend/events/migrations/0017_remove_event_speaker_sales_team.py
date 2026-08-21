from django.db import migrations


def fold_speaker_into_sca(apps, schema_editor):
    """
    The Speaker Sales team has been merged into the SCA (formerly Sales Team)
    team, so the event catalogue keeps one owner column instead of two. Any name
    that lived only in speaker_sales_team is moved across before the column goes,
    rather than being dropped on the floor; a row that already names an SCA is
    left exactly as it is, since that value is the one the sales_executive FK is
    kept in step with by Event.save().
    """
    Event = apps.get_model("events", "Event")
    moved = 0
    for pk, speaker in Event.objects.exclude(
        speaker_sales_team=""
    ).filter(sales_team="").values_list("pk", "speaker_sales_team"):
        Event.objects.filter(pk=pk).update(sales_team=speaker)
        moved += 1
    if moved:
        print(f"  folded speaker_sales_team into sales_team on {moved} event(s)")


def unfold(apps, schema_editor):
    """
    Irreversible in the honest sense: once the two columns are one, there is
    nothing recording which names arrived from the speaker column. Re-adding the
    field is left to the schema half of this migration; the values stay in
    sales_team.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0016_event_import_batch_id"),
    ]

    operations = [
        migrations.RunPython(fold_speaker_into_sca, unfold),
        migrations.RemoveField(
            model_name="event",
            name="speaker_sales_team",
        ),
    ]
