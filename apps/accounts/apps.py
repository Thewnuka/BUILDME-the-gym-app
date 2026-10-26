from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _create_role_groups(sender, **kwargs):
    from .services import ensure_role_groups

    ensure_role_groups()


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts"

    def ready(self):
        from . import signals  # noqa: F401

        post_migrate.connect(_create_role_groups, sender=self)
