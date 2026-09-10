from django.apps import AppConfig


class CreditControlConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "credit_control"
    verbose_name = "Credit Control"

    def ready(self):
        # Registers the post_save receiver that routes an invoice live. Imported
        # here rather than at module scope because the receiver touches models,
        # and importing those before the app registry is populated is the
        # classic AppRegistryNotReady.
        from . import signals  # noqa: F401
