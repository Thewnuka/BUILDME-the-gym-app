from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import Role
from apps.core.models import ActivityLog, Notification
from apps.core.notify import notify
from apps.core.utils import log_activity
from apps.members.models import Member
from apps.memberships.models import Membership

from .models import Announcement

A = Announcement.Audience


def audience_members(announcement):
    """Member profiles in the audience (active members only)."""
    if announcement.audience in (A.STAFF, A.ROLE):
        return Member.objects.none()
    members = Member.objects.active()
    if announcement.audience == A.ACTIVE_MEMBERSHIP:
        members = members.filter(pk__in=Membership.objects.current().values("member_id"))
    elif announcement.audience == A.PLAN:
        members = members.filter(pk__in=Membership.objects.current().filter(plan=announcement.plan_id)
                                 .values("member_id"))
    return members


def audience_staff(announcement):
    User = get_user_model()
    staff = User.objects.filter(is_active=True).exclude(role=Role.MEMBER)
    if announcement.audience in (A.EVERYONE, A.STAFF):
        return staff
    if announcement.audience == A.ROLE:
        return staff.filter(role=announcement.role)
    return User.objects.none()


def visible_to(user, qs=None):
    """Live announcements addressed to ``user``."""
    qs = (qs if qs is not None else Announcement.objects.all()).live()
    member = getattr(user, "member_profile", None) if user.role == Role.MEMBER else None
    visible = []
    for a in qs.select_related("plan"):
        if member is not None:
            if audience_members(a).filter(pk=member.pk).exists():
                visible.append(a)
        elif a.audience in (A.EVERYONE, A.STAFF) or (a.audience == A.ROLE and a.role == user.role):
            visible.append(a)
    return visible


@transaction.atomic
def publish_if_due(announcement, actor=None):
    """Notify the audience once, when the announcement is live. Returns notifications created."""
    a = Announcement.objects.select_for_update().get(pk=announcement.pk)
    now = timezone.now()
    if a.notified_at or not a.notify_recipients or not a.is_active or a.publish_at > now \
            or (a.expires_at and a.expires_at <= now):
        return 0
    created = 0
    link = a.get_absolute_url()
    message = a.body[:300]
    for member in audience_members(a).select_related("user"):
        if notify(member=member, title=a.title, message=message, link=link, category=Notification.Category.ANNOUNCEMENT,
                  dedupe_key=f"announcement:{a.pk}:m{member.pk}", external=a.send_external):
            created += 1
    for user in audience_staff(a):
        if notify(user=user, title=a.title, message=message, link=link, category=Notification.Category.ANNOUNCEMENT,
                  dedupe_key=f"announcement:{a.pk}:u{user.pk}"):
            created += 1
    a.notified_at = now
    a.save(update_fields=["notified_at"])
    log_activity(ActivityLog.Action.ANNOUNCEMENT_PUBLISHED, f"Announcement '{a.title}' sent to {created} recipient(s)",
                 actor=actor)
    return created


def publish_due():
    return sum(publish_if_due(a) for a in Announcement.objects.live().filter(notified_at__isnull=True,
                                                                              notify_recipients=True))
