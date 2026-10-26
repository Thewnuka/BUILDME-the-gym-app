from django.apps import AppConfig


class WorkoutsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workouts"
    label = "workouts"
    verbose_name = "Workouts"

    def ready(self):
        from . import metrics  # noqa: F401
