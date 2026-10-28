from django.apps import AppConfig


class CollectorsConfig(AppConfig):
    name = "cartio_controller.collectors"
    label = "collectors"
    verbose_name = "Customers and collectors"

    def ready(self):
        # Connects the sign-in receivers.
        from . import audit  # noqa: F401
