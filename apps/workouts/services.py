from django.db import transaction

from apps.core.models import ActivityLog
from apps.core.utils import log_activity

from .models import ProgressEntry, ProgressMeasurement, WorkoutDay, WorkoutItem, WorkoutLog, WorkoutLogEntry, WorkoutPlan


@transaction.atomic
def copy_plan_to_member(template, member, start_date, end_date, actor, request=None):
    """Give a member their own copy of a template, so later template edits don't change it."""
    plan = WorkoutPlan.objects.create(
        name=template.name, description=template.description, goal=template.goal, member=member, source=template,
        start_date=start_date, end_date=end_date, created_by=actor,
    )
    for day in template.days.prefetch_related("items"):
        new_day = WorkoutDay.objects.create(plan=plan, day_number=day.day_number, title=day.title)
        WorkoutItem.objects.bulk_create([
            WorkoutItem(day=new_day, exercise_id=i.exercise_id, order=i.order, sets=i.sets, reps=i.reps,
                        weight_kg=i.weight_kg, duration_seconds=i.duration_seconds, rest_seconds=i.rest_seconds,
                        notes=i.notes)
            for i in day.items.all()
        ])
    log_activity(ActivityLog.Action.WORKOUT_PLAN_ASSIGNED, f"Assigned workout plan '{plan.name}' to {member.member_id}",
                 actor=actor, request=request)
    return plan


@transaction.atomic
def log_workout(plan, day, performed_on, rows, notes, actor):
    """
    Record a completed workout. ``rows`` = [(item, completed, sets, reps, weight)].
    The planned prescription and exercise name are copied onto each entry.
    """
    log = WorkoutLog.objects.create(member=plan.member, plan=plan, plan_name=plan.name, day_label=str(day),
                                    performed_on=performed_on, notes=notes[:255], logged_by=actor)
    WorkoutLogEntry.objects.bulk_create([
        WorkoutLogEntry(log=log, exercise=item.exercise, exercise_name=item.exercise.name, planned=item.prescription,
                        completed=completed, sets_done=sets, reps_done=reps, weight_kg=weight)
        for item, completed, sets, reps, weight in rows
    ])
    return log


@transaction.atomic
def record_progress(member, form, actor):
    entry = form.save(commit=False)
    entry.member = member
    entry.recorded_by = actor
    entry.save()
    ProgressMeasurement.objects.bulk_create([
        ProgressMeasurement(entry=entry, measurement_type=mt, value=value) for mt, value in form.measurement_values()
    ])
    return entry


def progress_series(member):
    """Chart data: weight and each measurement type over time (oldest first)."""
    entries = list(ProgressEntry.objects.filter(member=member).prefetch_related("measurements__measurement_type")
                   .order_by("recorded_on", "pk"))
    weight = [{"x": e.recorded_on.isoformat(), "y": float(e.weight_kg)} for e in entries if e.weight_kg is not None]
    measures = {}
    for e in entries:
        for m in e.measurements.all():
            measures.setdefault(str(m.measurement_type), []).append({"x": e.recorded_on.isoformat(), "y": float(m.value)})
    return {"weight": weight, "measurements": measures}
