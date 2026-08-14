from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    UserPermission: one person's DELTA from their team, per module.

    Split from the removal of CustomRole (0024) so that 0023 has both the old
    tables and the new ones available to copy between. A single auto-generated
    migration would have dropped the source before anything could read it.
    """

    dependencies = [
        ("accounts", "0021_alter_user_custom_role"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserPermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("module", models.CharField(max_length=50)),
                ("can_view", models.BooleanField(default=None, null=True)),
                ("can_create", models.BooleanField(default=None, null=True)),
                ("can_update", models.BooleanField(default=None, null=True)),
                ("can_delete", models.BooleanField(default=None, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name="permission_overrides",
                                           to="accounts.user")),
            ],
            options={
                "db_table": "user_permissions",
                "ordering": ["module"],
                "unique_together": {("user", "module")},
            },
        ),
    ]
