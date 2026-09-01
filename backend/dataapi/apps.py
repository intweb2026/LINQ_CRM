from django.apps import AppConfig


class DataapiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dataapi"
    verbose_name = "Data API"

    def ready(self):
        """
        Wire the deletion tombstones.

        Connected here rather than at import time in models.py: the senders
        live in four other apps, and get_model() inside ready() is the one
        place Django guarantees every app's models are already loaded.
        dispatch_uid keeps a second ready() call idempotent.
        """
        from django.apps import apps
        from django.db.models.signals import post_delete

        from .models import DELETION_SOURCES, record_deletion

        for label, resource in DELETION_SOURCES.items():
            post_delete.connect(
                record_deletion,
                sender=apps.get_model(label),
                dispatch_uid=f"dataapi.tombstone.{resource}",
            )
