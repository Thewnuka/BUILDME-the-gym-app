from django import template
from django.conf import settings

from apps.trainers.access import can_coach, can_view_member_health
from apps.trainers.forms import AssignTrainerForm
from apps.trainers.models import TrainerAssignment

register = template.Library()


@register.inclusion_tag("trainers/_member_panel.html", takes_context=True)
def member_training(context, member):
    """Trainers, workout plans, progress and nutrition links for one member (permission-aware)."""
    from apps.appointments.models import Appointment

    user = context["request"].user
    staff_view = (user.has_perm("accounts.view_training") or user.has_perm("accounts.manage_trainers")
                  or user.has_perm("accounts.manage_appointments"))
    health = can_view_member_health(user, member)
    return {
        "member": member,
        "visible": staff_view or health,
        "assignments": TrainerAssignment.objects.active().filter(member=member).select_related("trainer__user"),
        "can_assign": user.has_perm("accounts.manage_trainers") and member.is_active,
        "assign_form": AssignTrainerForm() if user.has_perm("accounts.manage_trainers") else None,
        "can_book": user.has_perm("accounts.manage_appointments") and member.is_active,
        "upcoming": Appointment.objects.upcoming().filter(member=member).select_related("trainer__user")[:3]
        if staff_view else [],
        "health": health,
        "can_coach": can_coach(user, member),
        "plans": member.workout_plans.all()[:5] if health else [],
        "nutrition_enabled": settings.NUTRITION_ENABLED,
    }
