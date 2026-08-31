"""
Backfill "Added User" for tickets raised in this CRM.

added_user_text was only ever written by the importer (Zoho's own "Added User"),
so every ticket created through the UI or the webhook showed the column blank
even though created_by recorded exactly who raised it. Forward-fill from
created_by; new rows are stamped at create time from now on.
"""
from django.conf import settings
from django.db import migrations
from django.db.models import OuterRef, Subquery, Value
from django.db.models.functions import Coalesce, Concat, NullIf, Trim


def fill(apps, schema_editor):
    Ticket = apps.get_model("ticket_central", "Ticket")
    User = apps.get_model(settings.AUTH_USER_MODEL)

    # Same rule as utils.display_name: full name, else username.
    #
    # A SUBQUERY, not update(added_user_text=F("created_by__first_name")…):
    # an UPDATE cannot join, and Django refuses a joined field reference in one.
    # Done in SQL either way rather than row by row — this table holds ~43,000
    # tickets.
    name = (
        User.objects.filter(pk=OuterRef("created_by"))
        .annotate(disp=Coalesce(
            NullIf(Trim(Concat("first_name", Value(" "), "last_name")), Value("")),
            "username",
        ))
        .values("disp")[:1]
    )
    Ticket.objects.filter(added_user_text="", created_by__isnull=False).update(
        added_user_text=Subquery(name)
    )


class Migration(migrations.Migration):

    dependencies = [
        ("ticket_central", "0007_ticket_trgm_indexes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # No reverse: the blanks it replaced are not worth recording, and putting
        # them back would erase whatever an import has written since.
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
