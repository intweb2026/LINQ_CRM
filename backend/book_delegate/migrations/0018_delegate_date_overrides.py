"""
Per-delegate Request Date and Invoice Date overrides.

Two nullable date columns, null meaning "inherit the invoice", exactly like the
five delegate_* payment overrides added before them. Nothing is backfilled; a
null row reads the invoice's date, which is what every existing row already
showed, so the data is unchanged by this migration and the columns start empty.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("book_delegate", "0017_delegate_person_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookdelegate",
            name="delegate_request_date",
            field=models.DateField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="bookdelegate",
            name="delegate_invoice_date",
            field=models.DateField(blank=True, default=None, null=True),
        ),
    ]
