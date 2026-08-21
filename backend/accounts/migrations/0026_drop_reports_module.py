"""
Delete the permission rows for the retired "reports" module.

The Reports page is gone, so "reports" left accounts.models.CRM_MODULES. The
matrix a request resolves against is generated from that list at request time,
which means these rows already influence nothing — but they are the only place a
module name is stored rather than derived, so leaving them behind would make the
permissions grid re-grow a column for a page that does not exist the moment
anything iterates the table instead of the list.

Irreversible in the honest sense: the reverse could recreate rows with all four
cells False, but not the grants that were actually there, so it declines rather
than pretending. Nothing depends on the rows existing.
"""
from django.db import migrations

MODULE = "reports"


def drop_reports_rows(apps, schema_editor):
    TeamPermission = apps.get_model("teams", "TeamPermission")
    UserPermission = apps.get_model("accounts", "UserPermission")
    TeamPermission.objects.filter(module=MODULE).delete()
    UserPermission.objects.filter(module=MODULE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0025_perf_indexes"),
        ("teams", "0003_team_permissions"),
    ]

    operations = [
        migrations.RunPython(drop_reports_rows, migrations.RunPython.noop),
    ]
