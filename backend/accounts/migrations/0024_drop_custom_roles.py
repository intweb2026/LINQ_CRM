"""
Drop the per-user CustomRole now that 0023 has moved everything onto teams.

OPERATION ORDER IS LOad-BEARING and is not what makemigrations produced. It
emitted RemoveField(rolepermission.custom_role) BEFORE
AlterUniqueTogether(rolepermission), and the unique constraint is ON that
column — so the schema editor went to drop the constraint, asked the model state
for a field that had just been removed, and died with

    FieldDoesNotExist: RolePermission has no field named 'custom_role'

The constraint has to go first, then the column it covered.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0023_permissions_onto_teams'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='custom_role',
        ),
        migrations.AlterUniqueTogether(
            name='rolepermission',
            unique_together=None,
        ),
        migrations.RemoveField(
            model_name='rolepermission',
            name='custom_role',
        ),
        migrations.DeleteModel(
            name='RolePermission',
        ),
        migrations.DeleteModel(
            name='CustomRole',
        ),
    ]
