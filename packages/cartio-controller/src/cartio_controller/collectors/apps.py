from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CollectorsConfig(AppConfig):
    name = "cartio_controller.collectors"
    label = "collectors"
    verbose_name = "Customers and collectors"

    def ready(self):
        # Connects the sign-in receivers.
        from . import audit  # noqa: F401
        from .db_grants import grant_after_migrate

        # Every app's tables exist by the time this app's post_migrate fires.
        post_migrate.connect(grant_after_migrate, sender=self, dispatch_uid="cartio-db-grants")
