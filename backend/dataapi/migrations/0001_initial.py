"""
State-only migration.

The actual DDL lives in 0001_initial_data_api_keys.sql and is applied BY HAND,
per the project rule that schema changes are hand-run SQL rather than
`manage.py migrate`. This file exists so Django's migration state knows the
table and does not offer to create it again on `makemigrations`.
"""
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DataApiKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("key_hash", models.CharField(
                    db_index=True, max_length=64, unique=True,
                    help_text="SHA-256 hex digest of the raw API key")),
                ("key_preview", models.CharField(
                    blank=True, default="", max_length=20,
                    help_text="First 8 + last 4 chars for admin display")),
                ("scopes", models.JSONField(
                    blank=True, default=list,
                    help_text='Allowed resources, e.g. ["bookings","delegates"]. Empty = all.')),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("rate_limit_per_minute", models.PositiveIntegerField(default=60)),
                ("expires_at", models.DateTimeField(
                    blank=True, null=True, help_text="Null = never expires")),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("usage_count", models.PositiveIntegerField(default=0)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_data_api_keys",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "data_api_keys",
                "ordering": ["-created_at"],
                "verbose_name": "Data API Key",
                "verbose_name_plural": "Data API Keys",
            },
        ),
    ]
