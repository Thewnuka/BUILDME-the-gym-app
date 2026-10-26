"""
Booking rules (approved for Phase 4):

* A session must fit entirely inside one of the trainer's weekly availability
  windows, on a day the trainer is not on time off, in the future and within
  APPOINTMENT_BOOKING_HORIZON_DAYS.
* No overlaps: a trainer (and a member) can't have two non-cancelled sessions
  at the same time. The trainer row is locked during booking, so two
  simultaneous requests for the same slot are serialised; the database also
  rejects duplicate start times for a trainer.
* The member must be active and hold a non-cancelled, non-suspended membership
  covering the session date.
* Members booking for themselves may book only trainers currently assigned to
  them, and may cancel/reschedule only up to APPOINTMENT_MEMBER_CHANGE_CUTOFF_HOURS
  before the start. Staff with manage_appointments may do both at any time.
* Fees are a snapshot of the session type. Payments use the existing ledger
  (Payment.appointment); cancelling stops the charge, refunds stay manual.
"""
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core import gym
from apps.core.models import ActivityLog, Notification
from apps.core.notify import notify as core_notify
from apps.core.utils import log_activity
from apps.memberships.models import Membership
from apps.trainers.models import TimeOff, TrainerAssignment, TrainerProfile

from .models import Appointment, AppointmentEvent, SessionType

Action = ActivityLog.Action


class BookingError(Exception):
    """A booking request violates a scheduling rule."""


def _local(dt):
    return timezone.localtime(dt)


def _aware(day, t):
    return timezone.make_aware(datetime.combine(day, t))


def _notify_parties(appointment, title, message, actor):
    """Member (in-app + configured external channels) and trainer (in-app), except whoever made the change."""
    member = appointment.member
    if not (member.user_id and member.user_id == getattr(actor, "pk", None)):
        core_notify(member=member, title=title, message=message, link=reverse_member_link(appointment),
                    category=Notification.Category.APPOINTMENT, external=True)
    trainer_user = appointment.trainer.user
    if trainer_user != actor:
        core_notify(user=trainer_user, title=title, message=message, link=appointment.get_absolute_url(),
                    category=Notification.Category.APPOINTMENT)


def reverse_member_link(appointment):
    from django.urls import reverse

    return reverse("appointments:my_detail", args=[appointment.pk])


def describe(appointment):
    start = _local(appointment.starts_at)
    return f"{appointment.session_type.name} with {appointment.trainer.name} on {start:%a %d %b at %H:%M}"


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
def is_on_time_off(trainer, day):
    return TimeOff.objects.filter(trainer=trainer, start_date__lte=day, end_date__gte=day).exists()


def available_slots(trainer, day, duration_minutes, exclude=None):
    """Free start times (aware datetimes) for ``trainer`` on ``day``."""
    now = timezone.now()
    horizon = timezone.localdate() + timedelta(days=settings.APPOINTMENT_BOOKING_HORIZON_DAYS)
    if day < timezone.localdate() or day > horizon or not trainer.is_bookable or is_on_time_off(trainer, day):
        return []
    step = timedelta(minutes=settings.APPOINTMENT_SLOT_STEP_MINUTES)
    length = timedelta(minutes=duration_minutes)
    busy = list(
        Appointment.objects.occupying().filter(trainer=trainer, starts_at__date__gte=day - timedelta(days=1),
                                               starts_at__date__lte=day + timedelta(days=1))
        .exclude(pk=getattr(exclude, "pk", None)).values_list("starts_at", "ends_at")
    )
    slots = []
    for window in trainer.availability.filter(weekday=day.weekday()):
        start = _aware(day, window.start_time)
        window_end = _aware(day, window.end_time)
        while start + length <= window_end:
            end = start + length
            if start > now and not any(b_start < end and b_end > start for b_start, b_end in busy):
                slots.append(start)
            start += step
    return sorted(set(slots))


def _check_slot(trainer, member, starts_at, duration_minutes, exclude=None):
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    local_start, local_end = _local(starts_at), _local(ends_at)
    if starts_at <= timezone.now():
        raise BookingError("Sessions must be booked for a future time.")
    horizon = timezone.localdate() + timedelta(days=settings.APPOINTMENT_BOOKING_HORIZON_DAYS)
    if local_start.date() > horizon:
        raise BookingError(f"Sessions can be booked up to {settings.APPOINTMENT_BOOKING_HORIZON_DAYS} days ahead.")
    if not trainer.is_bookable:
        raise BookingError(f"{trainer.name} is not taking bookings.")
    if is_on_time_off(trainer, local_start.date()):
        raise BookingError(f"{trainer.name} is not working on {local_start:%d %b}.")
    fits = trainer.availability.filter(
        weekday=local_start.weekday(), start_time__lte=local_start.time(), end_time__gte=local_end.time()
    ).exists() and local_start.date() == local_end.date()
    if not fits:
        raise BookingError(f"{trainer.name} is not available at that time.")
    others = Appointment.objects.overlapping(starts_at, ends_at).exclude(pk=getattr(exclude, "pk", None))
    if others.filter(trainer=trainer).exists():
        raise BookingError("That time has just been booked by someone else. Please choose another slot.")
    if others.filter(member=member).exists():
        raise BookingError("The member already has a session at that time.")
    return ends_at


def _check_member(member, day):
    if not member.is_active:
        raise BookingError("This member is inactive.")
    covering = Membership.objects.for_member(member).not_cancelled().filter(start_date__lte=day, end_date__gte=day)
    if not covering.exists():
        raise BookingError(f"{member.full_name} has no membership covering {day:%d %b %Y}.")
    if day == timezone.localdate() and not covering.exclude(state=Membership.State.SUSPENDED).exists():
        raise BookingError("The membership is suspended.")


def _check_member_may_book_trainer(member, trainer, day):
    if not TrainerAssignment.objects.active(day).filter(trainer=trainer, member=member).exists():
        raise BookingError(f"You can only book sessions with your assigned trainer(s). Ask the front desk to "
                           f"book {trainer.name} for you.")


def _lock_trainer(trainer):
    return TrainerProfile.objects.select_for_update().select_related("user").get(pk=trainer.pk)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def book(member, trainer, session_type, starts_at, actor, notes="", as_member=False, request=None):
    try:
        with transaction.atomic():
            trainer = _lock_trainer(trainer)
            # Read the session type fresh so the fee/duration snapshot is the current DB value.
            session_type = SessionType.objects.get(pk=session_type.pk)
            if not session_type.is_active:
                raise BookingError("This session type is no longer offered.")
            day = _local(starts_at).date()
            _check_member(member, day)
            if as_member:
                _check_member_may_book_trainer(member, trainer, day)
            ends_at = _check_slot(trainer, member, starts_at, session_type.duration_minutes)
            appointment = Appointment.objects.create(
                trainer=trainer, member=member, session_type=session_type, starts_at=starts_at, ends_at=ends_at,
                duration_minutes=session_type.duration_minutes, fee=session_type.fee, notes=notes.strip()[:255],
                booked_by=actor,
            )
            AppointmentEvent.objects.create(appointment=appointment, event_type=AppointmentEvent.Type.BOOKED,
                                            performed_by=actor, note="Booked online by member" if as_member else "")
    except IntegrityError:
        raise BookingError("That time has just been booked by someone else. Please choose another slot.")
    _notify_parties(appointment, "Session booked", describe(appointment), actor)
    log_activity(Action.APPOINTMENT_BOOKED, f"Booked {describe(appointment)} for {member.member_id}",
                 actor=actor, request=request)
    return appointment


def _check_member_change(appointment, as_member):
    if as_member and not appointment.member_can_change:
        raise BookingError(
            f"Sessions can be changed online up to {settings.APPOINTMENT_MEMBER_CHANGE_CUTOFF_HOURS} hours before "
            "they start. Please contact the front desk."
        )


def reschedule(appointment, new_start, actor, as_member=False, request=None):
    try:
        with transaction.atomic():
            trainer = _lock_trainer(appointment.trainer)
            appointment = Appointment.objects.select_for_update().select_related("member", "session_type").get(
                pk=appointment.pk)
            if appointment.status != Appointment.Status.BOOKED:
                raise BookingError("Only booked sessions can be rescheduled.")
            _check_member_change(appointment, as_member)
            _check_member(appointment.member, _local(new_start).date())
            if as_member:
                _check_member_may_book_trainer(appointment.member, trainer, _local(new_start).date())
            old = describe(appointment)
            ends_at = _check_slot(trainer, appointment.member, new_start, appointment.duration_minutes,
                                  exclude=appointment)
            appointment.starts_at, appointment.ends_at, appointment.reminder_sent_at = new_start, ends_at, None
            appointment.save(update_fields=["starts_at", "ends_at", "reminder_sent_at"])
            AppointmentEvent.objects.create(appointment=appointment, event_type=AppointmentEvent.Type.RESCHEDULED,
                                            performed_by=actor, note=f"Moved from {old.split(' on ')[-1]}")
    except IntegrityError:
        raise BookingError("That time has just been booked by someone else. Please choose another slot.")
    _notify_parties(appointment, "Session rescheduled", describe(appointment), actor)
    log_activity(Action.APPOINTMENT_RESCHEDULED, f"Rescheduled session for {appointment.member.member_id}",
                 actor=actor, request=request)
    return appointment


@transaction.atomic
def cancel(appointment, actor, reason, as_member=False, request=None):
    appointment = Appointment.objects.select_for_update().select_related("member", "trainer").get(pk=appointment.pk)
    reason = (reason or "").strip()
    if appointment.status != Appointment.Status.BOOKED:
        raise BookingError("Only booked sessions can be cancelled.")
    if not reason:
        raise BookingError("Please give a reason for cancelling.")
    _check_member_change(appointment, as_member)
    appointment.status = Appointment.Status.CANCELLED
    appointment.cancelled_at = timezone.now()
    appointment.cancellation_reason = reason[:255]
    appointment.save(update_fields=["status", "cancelled_at", "cancellation_reason"])
    AppointmentEvent.objects.create(appointment=appointment, event_type=AppointmentEvent.Type.CANCELLED,
                                    performed_by=actor, note=reason[:255])
    _notify_parties(appointment, "Session cancelled",
                    f"{describe(appointment)}: {reason[:200]}", actor)
    log_activity(Action.APPOINTMENT_CANCELLED, f"Cancelled session for {appointment.member.member_id}",
                 actor=actor, request=request)
    return appointment


@transaction.atomic
def mark_outcome(appointment, actor, attended=True, note="", request=None):
    appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
    if appointment.status != Appointment.Status.BOOKED:
        raise BookingError("This session has already been closed.")
    if not appointment.has_started:
        raise BookingError("A session can only be completed once it has started.")
    appointment.status = Appointment.Status.COMPLETED if attended else Appointment.Status.NO_SHOW
    appointment.save(update_fields=["status"])
    AppointmentEvent.objects.create(
        appointment=appointment, performed_by=actor, note=note[:255],
        event_type=AppointmentEvent.Type.COMPLETED if attended else AppointmentEvent.Type.NO_SHOW,
    )
    if attended:
        log_activity(Action.APPOINTMENT_COMPLETED, f"Completed session for {appointment.member.member_id}",
                     actor=actor, request=request)
    return appointment


def send_reminders(now=None):
    """
    Notify members (in-app + configured channels) and trainers about sessions starting within
    APPOINTMENT_REMINDER_HOURS. Idempotent: each session is reminded once
    (again after a reschedule). Run from cron: ``manage.py send_appointment_reminders``.
    """
    now = now or timezone.now()
    due = Appointment.objects.filter(
        status=Appointment.Status.BOOKED, reminder_sent_at__isnull=True,
        starts_at__gt=now, starts_at__lte=now + timedelta(hours=gym.appointment_reminder_hours()),
    ).select_related("member__user", "trainer__user", "session_type")
    sent = 0
    for appointment in due:
        with transaction.atomic():
            locked = Appointment.objects.select_for_update().get(pk=appointment.pk)
            if locked.reminder_sent_at is not None:
                continue
            locked.reminder_sent_at = now
            locked.save(update_fields=["reminder_sent_at"])
            AppointmentEvent.objects.create(appointment=locked, event_type=AppointmentEvent.Type.REMINDED)
        text = describe(appointment)
        key = f"appointment_reminder:{appointment.pk}:{appointment.starts_at:%Y%m%d%H%M}"
        core_notify(member=appointment.member, title="Upcoming session reminder", message=text,
                    link=reverse_member_link(appointment), category=Notification.Category.APPOINTMENT,
                    dedupe_key=f"{key}:member", external=True)
        core_notify(user=appointment.trainer.user, title="Upcoming session reminder", message=text,
                    link=appointment.get_absolute_url(), category=Notification.Category.APPOINTMENT,
                    dedupe_key=f"{key}:trainer")
        sent += 1
    return sent
