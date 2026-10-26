from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .services import ensure_profile_for_user


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_member_profile(sender, instance, created, raw=False, **kwargs):
    if not created or raw or getattr(instance, "_member_profile", None) is not None:
        return
    ensure_profile_for_user(instance)
