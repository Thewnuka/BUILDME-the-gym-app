import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

WEEKDAYS = [(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"), (4, "Friday"), (5, "Saturday"),
            (6, "Sunday")]


def trainer_photo_path(instance, filename):
    return f"trainers/photos/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


def trainer_certificate_path(instance, filename):
    return f"trainers/certificates/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class TrainerQuerySet(models.QuerySet):
    def active(self):
        return self.filter(employment_status=TrainerProfile.Status.ACTIVE, user__is_active=True)


class TrainerProfile(models.Model):
    """Profile for a Trainer-role login. Created automatically with the login."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ON_LEAVE = "on_leave", "On leave"
        INACTIVE = "inactive", "Inactive"

    class EmploymentType(models.TextChoices):
        FULL_TIME = "full_time", "Full-time"
        PART_TIME = "part_time", "Part-time"
        CONTRACT = "contract", "Contract / freelance"

    STATUS_BADGES = {"active": "success", "on_leave": "warning", "inactive": "secondary"}

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="trainer_profile")
    phone = models.CharField(max_length=20, blank=True)
    bio = models.TextField(blank=True)
    qualifications = models.TextField(blank=True, help_text="One per line, e.g. 'NASM Certified Personal Trainer'.")
    specialties = models.CharField(max_length=255, blank=True, help_text="Comma-separated, e.g. 'Strength, Weight loss'.")
    photo = models.ImageField(upload_to=trainer_photo_path, blank=True)
    employment_status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    employment_type = models.CharField(max_length=10, choices=EmploymentType.choices, default=EmploymentType.FULL_TIME)
    hired_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TrainerQuerySet.as_manager()

    class Meta:
        ordering = ["user__first_name", "user__last_name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("trainers:detail", args=[self.pk])

    @property
    def name(self):
        return self.user.get_full_name() or self.user.username

    @property
    def initials(self):
        return self.user.initials

    @property
    def is_bookable(self):
        return self.employment_status == self.Status.ACTIVE and self.user.is_active

    @property
    def status_badge(self):
        return self.STATUS_BADGES.get(self.employment_status, "secondary")

    @property
    def specialty_list(self):
        return [s.strip() for s in self.specialties.split(",") if s.strip()]

    @property
    def qualification_list(self):
        return [q.strip() for q in self.qualifications.splitlines() if q.strip()]


class TrainerCertificate(models.Model):
    trainer = models.ForeignKey(TrainerProfile, on_delete=models.CASCADE, related_name="certificates")
    title = models.CharField(max_length=150)
    issuer = models.CharField(max_length=150, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    file = models.FileField(upload_to=trainer_certificate_path, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_on", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(expires_on__isnull=True) | Q(issued_on__isnull=True) | Q(expires_on__gte=F("issued_on")),
                name="certificate_expiry_after_issue",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def is_expired(self):
        return bool(self.expires_on and self.expires_on < timezone.localdate())


class Availability(models.Model):
    """A recurring weekly window in which the trainer accepts sessions."""

    trainer = models.ForeignKey(TrainerProfile, on_delete=models.CASCADE, related_name="availability")
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["weekday", "start_time"]
        verbose_name_plural = "availability"
        constraints = [
            models.CheckConstraint(condition=Q(start_time__lt=F("end_time")), name="availability_start_before_end"),
            models.CheckConstraint(condition=Q(weekday__lte=6), name="availability_weekday_valid"),
        ]

    def __str__(self):
        return f"{self.get_weekday_display()} {self.start_time:%H:%M}–{self.end_time:%H:%M}"


class TimeOff(models.Model):
    trainer = models.ForeignKey(TrainerProfile, on_delete=models.CASCADE, related_name="time_off")
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="timeoff_end_after_start"),
        ]

    def __str__(self):
        return f"{self.start_date:%d %b} – {self.end_date:%d %b %Y}"


class AssignmentQuerySet(models.QuerySet):
    def active(self, on=None):
        on = on or timezone.localdate()
        # end_date is exclusive: an assignment ended today no longer grants access today.
        return self.filter(start_date__lte=on).filter(Q(end_date__isnull=True) | Q(end_date__gt=on))


class TrainerAssignment(models.Model):
    """A trainer coaching a member. Ended assignments are kept as history (end_date is exclusive)."""

    trainer = models.ForeignKey(TrainerProfile, on_delete=models.PROTECT, related_name="assignments")
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="trainer_assignments")
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)
    goal = models.CharField(max_length=255, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AssignmentQuerySet.as_manager()

    class Meta:
        ordering = ["-start_date", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")), name="assignment_end_after_start"
            ),
            models.UniqueConstraint(
                fields=["trainer", "member"], condition=Q(end_date__isnull=True), name="assignment_one_open_per_pair"
            ),
        ]

    def __str__(self):
        return f"{self.trainer} → {self.member}"

    @property
    def is_active(self):
        today = timezone.localdate()
        return self.start_date <= today and (self.end_date is None or self.end_date > today)


class CompensationRecord(models.Model):
    """Salary/commission entries. Visible only with view_trainer_pay."""

    class Kind(models.TextChoices):
        SALARY = "salary", "Salary"
        COMMISSION = "commission", "Commission"
        BONUS = "bonus", "Bonus"
        DEDUCTION = "deduction", "Deduction"

    trainer = models.ForeignKey(TrainerProfile, on_delete=models.PROTECT, related_name="pay_records")
    period = models.DateField(help_text="Any date in the month this entry belongs to.")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    amount = models.DecimalField("amount (LKR)", max_digits=10, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="compensation_amount_positive")]

    def __str__(self):
        return f"{self.get_kind_display()} {self.period:%b %Y}"

    @property
    def signed_amount(self):
        return -self.amount if self.kind == self.Kind.DEDUCTION else self.amount
