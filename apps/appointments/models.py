from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

DURATION_CHOICES = [(m, f"{m} minutes") for m in (15, 30, 45, 60, 75, 90, 120)]


class SessionType(models.Model):
    """A bookable kind of session, e.g. '60-minute personal training'."""

    name = models.CharField(max_length=100, unique=True)
    duration_minutes = models.PositiveSmallIntegerField(choices=DURATION_CHOICES, default=60)
    fee = models.DecimalField("fee (LKR)", max_digits=10, decimal_places=2, default=0)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_active", "duration_minutes", "name"]
        constraints = [
            models.CheckConstraint(condition=Q(fee__gte=0), name="session_type_fee_not_negative"),
            models.CheckConstraint(condition=Q(duration_minutes__gte=15, duration_minutes__lte=240),
                                   name="session_type_duration_valid"),
        ]

    def __str__(self):
        return self.name


class AppointmentQuerySet(models.QuerySet):
    def upcoming(self):
        return self.filter(status=Appointment.Status.BOOKED, starts_at__gte=timezone.now()).order_by("starts_at")

    def occupying(self):
        """Appointments that block the trainer's/member's time."""
        return self.exclude(status=Appointment.Status.CANCELLED)

    def overlapping(self, start, end):
        return self.occupying().filter(starts_at__lt=end, ends_at__gt=start)


class Appointment(models.Model):
    """A personal training session. Rescheduling moves it in place; history is in AppointmentEvent."""

    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        COMPLETED = "completed", "Completed"
        NO_SHOW = "no_show", "No-show"
        CANCELLED = "cancelled", "Cancelled"

    STATUS_BADGES = {"booked": "primary", "completed": "success", "no_show": "warning", "cancelled": "secondary"}

    trainer = models.ForeignKey("trainers.TrainerProfile", on_delete=models.PROTECT, related_name="appointments")
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="appointments")
    session_type = models.ForeignKey(SessionType, on_delete=models.PROTECT, related_name="appointments")
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    # Snapshots taken at booking time so later session-type edits don't change agreements.
    duration_minutes = models.PositiveSmallIntegerField()
    fee = models.DecimalField("fee (LKR)", max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.BOOKED, db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    booked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)

    objects = AppointmentQuerySet.as_manager()

    class Meta:
        ordering = ["-starts_at", "-pk"]
        indexes = [models.Index(fields=["trainer", "starts_at"], name="appointment_trainer_start")]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="appointment_end_after_start"),
            models.CheckConstraint(condition=Q(fee__gte=0), name="appointment_fee_not_negative"),
            models.CheckConstraint(
                condition=~Q(status="cancelled") | Q(cancelled_at__isnull=False), name="appointment_cancel_has_time"
            ),
            # Last line of defence against double booking: identical start times
            # for one trainer are rejected by the database itself.
            models.UniqueConstraint(fields=["trainer", "starts_at"], condition=~Q(status="cancelled"),
                                    name="appointment_trainer_unique_start"),
        ]

    def __str__(self):
        return f"{self.session_type} · {self.member} with {self.trainer} · {timezone.localtime(self.starts_at):%d %b %H:%M}"

    def get_absolute_url(self):
        return reverse("appointments:detail", args=[self.pk])

    @property
    def status_badge(self):
        return self.STATUS_BADGES.get(self.status, "secondary")

    @property
    def is_upcoming(self):
        return self.status == self.Status.BOOKED and self.starts_at >= timezone.now()

    @property
    def has_started(self):
        return self.starts_at <= timezone.now()

    @property
    def member_can_change(self):
        """Within the online change window for members."""
        cutoff = timedelta(hours=settings.APPOINTMENT_MEMBER_CHANGE_CUTOFF_HOURS)
        return self.status == self.Status.BOOKED and self.starts_at - timezone.now() >= cutoff


class AppointmentEvent(models.Model):
    class Type(models.TextChoices):
        BOOKED = "booked", "Booked"
        RESCHEDULED = "rescheduled", "Rescheduled"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"
        NO_SHOW = "no_show", "Marked no-show"
        REMINDED = "reminded", "Reminder sent"

    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=12, choices=Type.choices)
    note = models.CharField(max_length=255, blank=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.get_event_type_display()
