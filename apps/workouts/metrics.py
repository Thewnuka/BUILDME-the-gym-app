from apps.dashboard.registry import register
from apps.trainers.access import trainer_profile_for
from apps.trainers.models import TrainerAssignment

from .models import WorkoutPlan


@register("workout_plans_assigned")
def workout_plans_assigned(user):
    qs = WorkoutPlan.objects.current()
    profile = trainer_profile_for(user)
    if profile and not user.has_perm("accounts.view_member_health"):
        qs = qs.filter(member_id__in=TrainerAssignment.objects.active().filter(trainer=profile).values("member_id"))
        return {"value": qs.count(), "note": "Your members' current plans"}
    return {"value": qs.count(), "note": "Current member plans"}
