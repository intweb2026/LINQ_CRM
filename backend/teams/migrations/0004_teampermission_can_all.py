"""
Per-module row scope: TeamPermission.can_all.

Backfilled False for every existing row, which is what the app already does
today: outside an is_all_access team or the admin role, nobody sees rows beyond
their assigned events. Nothing widens by this migration.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("teams", "0003_team_permissions")]

    operations = [
        migrations.AddField(
            model_name="teampermission",
            name="can_all",
            field=models.BooleanField(default=False),
        ),
    ]
