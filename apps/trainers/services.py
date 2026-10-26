from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.services import send_account_setup_email
from apps.core.models import ActivityLog
from apps.core.utils import log_activity

from .models import TrainerAssignment, TrainerProfile

Action = ActivityLog.Action


class TrainerError(Exception):
    """A trainer operation violates a business rule."""


def ensure_profile(user):
    """Every Trainer-role login has a profile (kept if the role later changes)."""
    if user.role == Role.TRAINER:
        TrainerProfile.objects.get_or_create(user=user)


@transaction.atomic
def create_trainer(form, actor, request):
    """Create a Trainer login + profile and email a password-setup link."""
    from apps.accounts.models import User

    data = form.cleaned_data
    user = User(
        username=data["username"], email=data["email"], first_name=data["first_name"], last_name=data["last_name"],
        phone=data.get("phone", ""), role=Role.TRAINER, created_by=actor,
    )
    user.set_unusable_password()
    user.save()
    profile = TrainerProfile.objects.get(user=user)  # created by the post_save signal
    for field in ("phone", "employment_type", "hired_on", "specialties"):
        setattr(profile, field, data.get(field) or getattr(profile, field))
    profile.save()
    log_activity(Action.USER_CREATED, f"Created Trainer account '{user.username}'", actor=actor, request=request)
    send_account_setup_email(request, user)
    return profile


@transaction.atomic
def assign_trainer(trainer, member, actor, start_date=None, goal="", request=None):
    trainer = TrainerProfile.objects.select_for_update().get(pk=trainer.pk)
    if not trainer.is_bookable:
        raise TrainerError("This trainer is not active.")
    if not member.is_active:
        raise TrainerError("This member is inactive.")
    if TrainerAssignment.objects.filter(trainer=trainer, member=member, end_date__isnull=True).exists():
        raise TrainerError(f"{member.full_name} is already assigned to {trainer.name}.")
    try:
        with transaction.atomic():
            assignment = TrainerAssignment.objects.create(
                trainer=trainer, member=member, start_date=start_date or timezone.localdate(),
                goal=goal.strip()[:255], assigned_by=actor,
            )
    except IntegrityError:
        raise TrainerError(f"{member.full_name} is already assigned to {trainer.name}.")
    log_activity(Action.TRAINER_ASSIGNED, f"Assigned {member.member_id} to trainer {trainer.name}",
                 actor=actor, request=request)
    return assignment


@transaction.atomic
def end_assignment(assignment, actor, request=None):
    assignment = TrainerAssignment.objects.select_for_update().get(pk=assignment.pk)
    if assignment.end_date is not None:
        raise TrainerError("This assignment has already ended.")
    today = timezone.localdate()
    assignment.end_date = max(today, assignment.start_date)
    assignment.ended_by = actor
    assignment.save(update_fields=["end_date", "ended_by"])
    log_activity(Action.TRAINER_UNASSIGNED,
                 f"Ended assignment of {assignment.member.member_id} to trainer {assignment.trainer.name}",
                 actor=actor, request=request)
    return assignment


def record_pay(trainer, form, actor, request=None):
    record = form.save(commit=False)
    record.trainer = trainer
    record.recorded_by = actor
    record.save()
    log_activity(Action.TRAINER_PAY_RECORDED, f"Recorded {record.get_kind_display().lower()} for trainer {trainer.name}",
                 actor=actor, request=request)  # amount deliberately not logged (pay is restricted)
    return record
