from datetime import datetime, time, timedelta

from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.appointments.models import SessionType
from apps.memberships.tests.factories import make_member, make_membership, make_plan
from apps.trainers.models import Availability, TrainerAssignment


def make_trainer(username=None, all_week=True):
    user = make_user(Role.TRAINER, username=username)
    profile = user.trainer_profile
    if all_week:
        for day in range(7):
            Availability.objects.create(trainer=profile, weekday=day, start_time=time(6), end_time=time(22))
    return profile


def make_active_member(**extra):
    today = timezone.localdate()
    member = make_member(joined_on=today - timedelta(days=30), **extra)
    make_membership(member, make_plan(months=3), today - timedelta(days=1))
    return member


def make_member_with_login(username):
    """A member profile linked to a Member-role login, with a current membership."""
    login = make_user(Role.MEMBER, username=username)
    member = login.member_profile
    member.joined_on = timezone.localdate() - timedelta(days=30)
    member.phone = f"+9471{login.pk:07d}"
    member.save()
    make_membership(member, make_plan(months=3), timezone.localdate() - timedelta(days=1))
    return login, member


def assign(trainer, member):
    return TrainerAssignment.objects.create(trainer=trainer, member=member)


def session_type(minutes=60, fee="0", name=None):
    return SessionType.objects.create(name=name or f"PT {minutes} {fee}", duration_minutes=minutes, fee=fee)


def at(days_ahead, hour, minute=0):
    day = timezone.localdate() + timedelta(days=days_ahead)
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))
