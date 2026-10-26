import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone


def exercise_image_path(instance, filename):
    return f"exercises/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class Exercise(models.Model):
    class MuscleGroup(models.TextChoices):
        CHEST = "chest", "Chest"
        BACK = "back", "Back"
        SHOULDERS = "shoulders", "Shoulders"
        ARMS = "arms", "Arms"
        CORE = "core", "Core"
        LEGS = "legs", "Legs"
        GLUTES = "glutes", "Glutes"
        FULL_BODY = "full_body", "Full body"
        CARDIO = "cardio", "Cardiovascular"

    class Category(models.TextChoices):
        STRENGTH = "strength", "Strength"
        CARDIO = "cardio", "Cardio"
        MOBILITY = "mobility", "Mobility / flexibility"
        HIIT = "hiit", "HIIT / conditioning"
        BALANCE = "balance", "Balance / stability"

    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True)
    muscle_group = models.CharField(max_length=12, choices=MuscleGroup.choices, db_index=True)
    category = models.CharField(max_length=10, choices=Category.choices, db_index=True)
    instructions = models.TextField(blank=True)
    image = models.ImageField(upload_to=exercise_image_path, blank=True)
    video_url = models.URLField("video link", blank=True, help_text="Optional link to a demonstration video.")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("workouts:exercise_detail", args=[self.pk])


class WorkoutPlanQuerySet(models.QuerySet):
    def templates(self):
        return self.filter(member__isnull=True)

    def assigned(self):
        return self.filter(member__isnull=False)

    def current(self, on=None):
        on = on or timezone.localdate()
        return self.assigned().filter(status=WorkoutPlan.Status.ACTIVE, start_date__lte=on).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=on))


class WorkoutPlan(models.Model):
    """A template (no member) or a member's own copy with start/end dates."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    goal = models.CharField(max_length=255, blank=True)
    member = models.ForeignKey("members.Member", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="workout_plans")
    source = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="copies",
                               help_text="Template this member plan was copied from.")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkoutPlanQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(member__isnull=True) | Q(start_date__isnull=False),
                                   name="workout_member_plan_has_start"),
            models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(start_date__isnull=True)
                                   | Q(end_date__gte=F("start_date")), name="workout_plan_end_after_start"),
        ]

    def __str__(self):
        return f"{self.name} ({self.member.full_name})" if self.member_id else self.name

    def get_absolute_url(self):
        return reverse("workouts:plan_detail", args=[self.pk])

    @property
    def is_template(self):
        return self.member_id is None

    @property
    def is_current(self):
        today = timezone.localdate()
        return (self.status == self.Status.ACTIVE and self.start_date and self.start_date <= today
                and (self.end_date is None or self.end_date >= today))


class WorkoutDay(models.Model):
    plan = models.ForeignKey(WorkoutPlan, on_delete=models.CASCADE, related_name="days")
    day_number = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=100, blank=True, help_text="e.g. 'Upper body' or 'Legs'.")

    class Meta:
        ordering = ["day_number"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "day_number"], name="workout_day_unique_number"),
            models.CheckConstraint(condition=Q(day_number__gte=1), name="workout_day_number_positive"),
        ]

    def __str__(self):
        return f"Day {self.day_number}" + (f": {self.title}" if self.title else "")


class WorkoutItem(models.Model):
    day = models.ForeignKey(WorkoutDay, on_delete=models.CASCADE, related_name="items")
    exercise = models.ForeignKey(Exercise, on_delete=models.PROTECT, related_name="+")
    order = models.PositiveSmallIntegerField(default=1)
    sets = models.PositiveSmallIntegerField(null=True, blank=True)
    reps = models.PositiveSmallIntegerField(null=True, blank=True)
    weight_kg = models.DecimalField("weight (kg)", max_digits=6, decimal_places=2, null=True, blank=True)
    duration_seconds = models.PositiveIntegerField("duration (seconds)", null=True, blank=True)
    rest_seconds = models.PositiveIntegerField("rest (seconds)", null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["order", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(weight_kg__isnull=True) | Q(weight_kg__gte=0),
                                   name="workout_item_weight_not_negative"),
            models.CheckConstraint(condition=Q(sets__isnull=True) | Q(sets__gte=1), name="workout_item_sets_positive"),
        ]

    def __str__(self):
        return f"{self.exercise.name} {self.prescription}"

    @property
    def prescription(self):
        parts = []
        if self.sets and self.reps:
            parts.append(f"{self.sets} × {self.reps}")
        elif self.sets:
            parts.append(f"{self.sets} sets")
        if self.weight_kg is not None:
            parts.append(f"@ {self.weight_kg.normalize():f} kg")
        if self.duration_seconds:
            m, s = divmod(self.duration_seconds, 60)
            parts.append(f"{m} min" + (f" {s} s" if s else "") if m else f"{s} s")
        if self.rest_seconds:
            parts.append(f"rest {self.rest_seconds} s")
        return " ".join(parts) or "—"


class WorkoutLog(models.Model):
    """A completed workout. Entries copy what was planned, so later plan edits never rewrite history."""

    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="workout_logs")
    plan = models.ForeignKey(WorkoutPlan, null=True, blank=True, on_delete=models.SET_NULL, related_name="logs")
    plan_name = models.CharField(max_length=120, blank=True)
    day_label = models.CharField(max_length=120, blank=True)
    performed_on = models.DateField(default=timezone.localdate, db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    logged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-performed_on", "-pk"]

    def __str__(self):
        return f"{self.plan_name} · {self.day_label} · {self.performed_on}"


class WorkoutLogEntry(models.Model):
    log = models.ForeignKey(WorkoutLog, on_delete=models.CASCADE, related_name="entries")
    exercise = models.ForeignKey(Exercise, null=True, on_delete=models.SET_NULL, related_name="+")
    exercise_name = models.CharField(max_length=120)
    planned = models.CharField(max_length=120, blank=True)
    completed = models.BooleanField(default=True)
    sets_done = models.PositiveSmallIntegerField(null=True, blank=True)
    reps_done = models.PositiveSmallIntegerField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["pk"]


class MeasurementType(models.Model):
    """Configurable body measurement, e.g. 'Waist' in cm."""

    name = models.CharField(max_length=60, unique=True)
    unit = models.CharField(max_length=12, default="cm")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit})"


class ProgressEntry(models.Model):
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="progress_entries")
    recorded_on = models.DateField(default=timezone.localdate, db_index=True)
    weight_kg = models.DecimalField("body weight (kg)", max_digits=5, decimal_places=1, null=True, blank=True)
    height_cm = models.DecimalField("height (cm)", max_digits=5, decimal_places=1, null=True, blank=True)
    body_fat_pct = models.DecimalField("body fat (%)", max_digits=4, decimal_places=1, null=True, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_on", "-pk"]
        verbose_name_plural = "progress entries"
        constraints = [
            models.CheckConstraint(condition=Q(weight_kg__isnull=True) | Q(weight_kg__gte=20, weight_kg__lte=400),
                                   name="progress_weight_range"),
            models.CheckConstraint(condition=Q(height_cm__isnull=True) | Q(height_cm__gte=50, height_cm__lte=260),
                                   name="progress_height_range"),
            models.CheckConstraint(condition=Q(body_fat_pct__isnull=True)
                                   | Q(body_fat_pct__gte=2, body_fat_pct__lte=75), name="progress_bodyfat_range"),
        ]

    def __str__(self):
        return f"{self.member} · {self.recorded_on}"


class ProgressMeasurement(models.Model):
    entry = models.ForeignKey(ProgressEntry, on_delete=models.CASCADE, related_name="measurements")
    measurement_type = models.ForeignKey(MeasurementType, on_delete=models.PROTECT, related_name="+")
    value = models.DecimalField(max_digits=6, decimal_places=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["entry", "measurement_type"], name="progress_measurement_unique"),
            models.CheckConstraint(condition=Q(value__gt=0), name="progress_measurement_positive"),
        ]
