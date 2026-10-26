"""
Single entry point for creating notifications.

    notify(user=..., title=..., dedupe_key="membership_expiring:12:7")

* In-app: the user sees it in the notification centre (bell).
* External: for member-facing messages pass ``external=True``; every channel
  listed in settings.NOTIFY_CHANNELS gets a NotificationDelivery row with an
  honest status (see channels.py).
* Duplicates: a ``dedupe_key`` is unique in the database, so running the
  scheduler twice (or two processes at once) never sends a reminder twice.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from . import channels, gym
from .models import Notification, NotificationDelivery

logger = logging.getLogger(__name__)


def notify(*, title, user=None, member=None, message="", link="", category=Notification.Category.SYSTEM,
           dedupe_key=None, external=False):
    """Create a notification (and external deliveries). Returns None if a duplicate or no recipient."""
    if member is not None and user is None and member.user_id and member.user.is_active:
        user = member.user
    if user is not None and not user.is_active:
        user = None
    if user is None and member is None:
        return None
    if dedupe_key and Notification.objects.filter(dedupe_key=dedupe_key).exists():
        return None
    try:
        with transaction.atomic():
            note = Notification.objects.create(
                user=user, member=member, title=title[:120], message=message[:500], link=link[:200],
                category=category, dedupe_key=dedupe_key[:150] if dedupe_key else None,
            )
    except IntegrityError:
        return None  # a concurrent run created it first
    if external and settings.NOTIFY_CHANNELS:
        _deliver_external(note, user, member)
    return note


def _deliver_external(note, user, member):
    email = (member.email if member and member.email else "") or (user.email if user else "")
    phone = (member.phone if member and member.phone else "") or (getattr(user, "phone", "") if user else "")
    body = f"{note.title}\n{note.message}".strip()
    for channel in settings.NOTIFY_CHANNELS:
        if channel not in NotificationDelivery.Channel.values:
            continue
        recipient = email if channel == NotificationDelivery.Channel.EMAIL else phone
        delivery = NotificationDelivery.objects.create(notification=note, channel=channel, recipient=recipient)
        try:
            if channel == NotificationDelivery.Channel.EMAIL:
                channels.send_email(delivery, f"{gym.gym_name()}: {note.title}",
                                    f"{body}\n\n— {gym.gym_name()}")
            elif channel == NotificationDelivery.Channel.SMS:
                channels.send_sms(delivery, f"{gym.gym_name()}: {body}")
            else:
                channels.send_whatsapp(delivery, f"{gym.gym_name()}: {body}")
        except Exception:  # never let a provider problem break the business action
            logger.exception("Delivery via %s failed", channel)
            channels._set(delivery, NotificationDelivery.Status.FAILED, "Unexpected error while sending")


def staff_with_perm(perm):
    """Active staff users holding ``perm`` (members are never included)."""
    User = get_user_model()
    return [u for u in User.objects.filter(is_active=True).exclude(role="member") if u.has_perm(perm)]


def notify_staff(perm, *, title, message="", link="", category=Notification.Category.SYSTEM, dedupe_key=None):
    """One notification per staff user with ``perm``; dedupe key gets the user id appended."""
    created = 0
    for user in staff_with_perm(perm):
        key = f"{dedupe_key}:u{user.pk}" if dedupe_key else None
        if notify(user=user, title=title, message=message, link=link, category=category, dedupe_key=key):
            created += 1
    return created
