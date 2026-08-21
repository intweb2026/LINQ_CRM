"""
Create a Data API key for external consumers.

Usage:
    python manage.py create_data_api_key "Google Sheets Sync" --scopes bookings,delegates,events
    python manage.py create_data_api_key "Full Export"

The raw key is printed ONCE and never stored; only its SHA-256 hash is kept.
Lose it and the only remedy is to create a new key and deactivate the old one.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from dataapi.models import DataApiKey

User = get_user_model()
SVC_USERNAME = "svc.sheets"
SVC_EMAIL = "svc.sheets@iq-hub.com"


class Command(BaseCommand):
    help = "Create a Data API key. The raw key is printed ONCE."

    def add_arguments(self, parser):
        parser.add_argument("name", help="Human-readable label for the key")
        parser.add_argument("--scopes", default="",
                            help="Comma-separated resource names (empty = all)")
        parser.add_argument("--rate-limit", type=int, default=60,
                            help="Requests per minute (default: 60)")
        parser.add_argument("--notes", default="", help="Optional notes")

    def handle(self, *args, **options):
        # Ensure the dedicated service account exists. It owns the key rows for
        # attribution only; the key itself never authenticates AS this user.
        svc_user, created = User.objects.get_or_create(
            username=SVC_USERNAME,
            defaults={
                "email": SVC_EMAIL,
                # NOT "admin". User.save() grants is_superuser and is_staff to
                # anything holding role="admin", so an admin service account
                # would be a superuser whose only job is to be the attribution
                # row on a read-only export key. "sales" carries no such grant,
                # and the key never authenticates AS this user anyway.
                "role": "sales",
                "is_active": True,
                "first_name": "Service",
                "last_name": "Sheets",
            },
        )
        if created:
            svc_user.set_unusable_password()
            svc_user.save()
            self.stdout.write(self.style.SUCCESS(
                f"Created service user: {SVC_USERNAME} ({SVC_EMAIL})"
            ))

        # Safety net, applied on EVERY run rather than only at creation, so that
        # re-running this command repairs a row an earlier run left elevated.
        #
        # The role is forced alongside the three flags on purpose. User.save()
        # re-grants is_superuser and is_staff whenever it sees role="admin", so
        # writing the flags while leaving a stale "admin" role in place would
        # hand them straight back inside this very save. role_is_explicit stops
        # the team-name derivation from having an opinion either way.
        svc_user.role = "sales"
        svc_user.role_is_explicit = True
        svc_user.is_superuser = False
        svc_user.is_staff = False
        svc_user.is_active = True
        svc_user.save(
            update_fields=["role", "is_superuser", "is_staff", "is_active"]
        )

        scopes = [s.strip() for s in options["scopes"].split(",") if s.strip()]

        instance, raw_key = DataApiKey.create_key(
            name=options["name"],
            created_by=svc_user,
            scopes=scopes,
            notes=options["notes"],
            rate_limit_per_minute=options["rate_limit"],
        )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("  DATA API KEY CREATED"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(f"  Name:    {instance.name}")
        self.stdout.write(f"  Scopes:  {scopes or 'ALL'}")
        self.stdout.write(f"  Preview: {instance.key_preview}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("  RAW KEY (shown ONCE - copy it now)"))
        self.stdout.write(self.style.WARNING(f"    {raw_key}"))
        self.stdout.write("")
        self.stdout.write("  Store this key in Google Apps Script:")
        self.stdout.write("    ScriptProperties.setProperty('CRM_API_KEY', '<the key>')")
        self.stdout.write(self.style.SUCCESS("=" * 60))
