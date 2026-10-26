from django.apps import AppConfig


class EquipmentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.equipment"
    label = "equipment"
    verbose_name = "Equipment"

    def ready(self):
        from . import metrics  # noqa: F401
