"""
Per-module row scope: UserPermission.can_all.

Three-state like its four siblings, so NULL is "inherit the team's answer" and
the default has to be None rather than False — a False backfill would read as a
deliberate revoke on every existing override row and would pin those people back
to their own rows even after their team was granted full visibility.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("accounts", "0028_user_login_access")]

    operations = [
        migrations.AddField(
            model_name="userpermission",
            name="can_all",
            field=models.BooleanField(default=None, null=True),
        ),
    ]
