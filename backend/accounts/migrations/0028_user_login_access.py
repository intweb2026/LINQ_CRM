from django.db import migrations, models


class Migration(migrations.Migration):
    """
    accounts.User.login_access.

    The production DDL is run by hand from backend/sql/0028_user_login_access.sql,
    which also INSERTs this migration's row into django_migrations. Django
    therefore never executes the operations below against production.

    They are not empty all the same. A database built purely from migrations —
    every test run, and any fresh deployment — has no other source for the
    column, and a state-only migration leaves it missing, which errors on the
    first query against the users table. So the DDL is mirrored here, written
    IF NOT EXISTS so that running the SQL file and the migration in either order
    on the same database is harmless.
    """

    dependencies = [
        ('accounts', '0027_add_google_sync_module'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='user',
                    name='login_access',
                    field=models.BooleanField(
                        default=True,
                        db_index=True,
                        help_text='When unchecked the user exists in the system but cannot sign in.',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=[
                        'ALTER TABLE users ADD COLUMN IF NOT EXISTS '
                        'login_access BOOLEAN NOT NULL DEFAULT TRUE;',
                        'CREATE INDEX IF NOT EXISTS users_login_access_idx '
                        'ON users (login_access);',
                    ],
                    reverse_sql=[
                        'DROP INDEX IF EXISTS users_login_access_idx;',
                        'ALTER TABLE users DROP COLUMN IF EXISTS login_access;',
                    ],
                ),
            ],
        ),
    ]
