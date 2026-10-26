from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .services import ensure_profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_trainer_profile(sender, instance, raw=False, **kwargs):
    if not raw:
        ensure_profile(instance)
