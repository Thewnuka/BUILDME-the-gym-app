"""Dashboard statistics from memberships."""
from django.db.models import Exists, OuterRef

from apps.core import gym
from apps.dashboard.registry import register
from apps.members.models import Member

from .models import Membership, MembershipStatus


@register("active_members")
def active_members(user):
    """Active members with a membership in good standing covering today."""
    current = Membership.objects.with_status(MembershipStatus.ACTIVE).filter(member=OuterRef("pk"))
    return {"value": Member.objects.active().filter(Exists(current)).count(), "note": "With a current membership"}


@register("expired_memberships")
def expired_memberships(user):
    """Active members whose last membership has ended and was not renewed."""
    count = Membership.objects.lapsed().filter(member__status=Member.Status.ACTIVE).values("member").distinct().count()
    return {"value": count, "note": "Not renewed"}


@register("upcoming_expirations")
def upcoming_expirations(user, limit=8):
    qs = Membership.objects.expiring_soon().select_related("member").order_by("end_date", "pk")
    return {"items": list(qs[:limit]), "total": qs.count(), "days": gym.expiring_soon_days()}
