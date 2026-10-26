"""
Who may see or change a member's training data.

Fitness, workout and nutrition records are personal health data. They are
visible to:
  * the member themself,
  * trainers with a *current* assignment to that member,
  * staff holding ``view_member_health`` (Super Admin and Gym Manager by default).

Creating plans/records for a member ("coaching") additionally requires
``manage_training``. Trainers can coach only members currently assigned to them.
"""
from .models import TrainerAssignment, TrainerProfile


def trainer_profile_for(user):
    if not user.is_authenticated:
        return None
    return TrainerProfile.objects.filter(user=user).first()


def member_profile_for(user):
    if not user.is_authenticated:
        return None
    return getattr(user, "member_profile", None)


def is_assigned_trainer(user, member, on=None):
    profile = trainer_profile_for(user)
    return bool(profile) and TrainerAssignment.objects.active(on).filter(trainer=profile, member=member).exists()


def is_self(user, member):
    return member.user_id is not None and member.user_id == user.pk


def can_view_member_health(user, member):
    if not user.is_authenticated:
        return False
    return (
        is_self(user, member)
        or user.has_perm("accounts.view_member_health")
        or (user.has_perm("accounts.view_training") and is_assigned_trainer(user, member))
    )


def can_coach(user, member):
    """Create or change workout plans, meal plans and progress records for ``member``."""
    if not user.is_authenticated or not user.has_perm("accounts.manage_training"):
        return False
    return user.has_perm("accounts.view_member_health") or is_assigned_trainer(user, member)


def coached_members(user):
    """Members whose training data ``user`` may manage."""
    from apps.members.models import Member

    if user.has_perm("accounts.view_member_health"):
        return Member.objects.active()
    profile = trainer_profile_for(user)
    if not profile:
        return Member.objects.none()
    ids = TrainerAssignment.objects.active().filter(trainer=profile).values("member_id")
    return Member.objects.filter(pk__in=ids)


def can_view_sessions(user):
    """Open the staff appointment pages (list and detail)."""
    return user.has_perm("accounts.manage_appointments") or user.has_perm("accounts.view_training")
