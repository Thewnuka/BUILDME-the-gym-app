"""Training dashboard figures. Trainers see their own numbers; managers see the whole gym."""
from django.utils import timezone

from apps.dashboard.registry import register
from apps.trainers.access import trainer_profile_for
from apps.trainers.models import TrainerProfile

from .models import Appointment


def _scope(user):
    qs = Appointment.objects.all()
    profile = trainer_profile_for(user)
    if profile and not user.has_perm("accounts.manage_appointments"):
        return qs.filter(trainer=profile), True
    return qs, False


@register("total_trainers")
def total_trainers(user):
    qs = TrainerProfile.objects.all()
    active = qs.filter(employment_status=TrainerProfile.Status.ACTIVE).count()
    return {"value": qs.count(), "note": f"{active} active"}


@register("upcoming_appointments")
def upcoming_appointments(user):
    qs, own = _scope(user)
    upcoming = qs.filter(status=Appointment.Status.BOOKED, starts_at__gte=timezone.now())
    today = upcoming.filter(starts_at__date=timezone.localdate()).count()
    return {"value": upcoming.count(), "note": f"{today} today" + (" · your schedule" if own else "")}


@register("completed_sessions")
def completed_sessions(user):
    qs, own = _scope(user)
    month_start = timezone.localdate().replace(day=1)
    done = qs.filter(status=Appointment.Status.COMPLETED, starts_at__date__gte=month_start).count()
    return {"value": done, "note": f"Since 1 {month_start:%b}" + (" · yours" if own else "")}
