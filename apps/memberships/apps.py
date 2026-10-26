from django.apps import AppConfig


class MembershipsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.memberships"
    label = "memberships"
    verbose_name = "Memberships"

    def ready(self):
        from . import metrics  # noqa: F401
