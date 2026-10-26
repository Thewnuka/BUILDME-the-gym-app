from django.apps import AppConfig


class TrainersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.trainers"
    label = "trainers"
    verbose_name = "Trainers"

    def ready(self):
        from . import signals  # noqa: F401
