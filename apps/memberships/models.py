from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Exists, F, OuterRef, Q
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone

from apps.core import gym

from . import periods


class DurationUnit(models.TextChoices):
    DAY = periods.DAY, "Days"
    MONTH = periods.MONTH, "Months"


# Standard durations offered in the plan form; anything else is "custom".
DURATION_PRESETS = {
    "monthly": ("Monthly (1 month)", 1, periods.MONTH),
    "quarterly": ("3 months", 3, periods.MONTH),
    "half_year": ("6 months", 6, periods.MONTH),
    "annual": ("Annual (12 months)", 12, periods.MONTH),
}
MAX_DURATION_MONTHS = 60
MAX_DURATION_DAYS = 1095

WEEKDAYS = [("0", "Mon"), ("1", "Tue"), ("2", "Wed"), ("3", "Thu"), ("4", "Fri"), ("5", "Sat"), ("6", "Sun")]
WEEKDAY_NAMES = dict(WEEKDAYS)


class AccessRulesMixin(models.Model):
    """
    Optional, machine-checkable access rules. Blank means unrestricted.
    access_days holds weekday numbers (Mon=0) as a comma-separated string.
    Windows are same-day: start must be before end (no overnight windows).
    """

    access_days = models.CharField("allowed days", max_length=20, blank=True)
    access_start_time = models.TimeField("access from", null=True, blank=True)
    access_end_time = models.TimeField("access until", null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def allowed_weekdays(self):
        return {int(d) for d in self.access_days.split(",") if d.strip().isdigit()}

    @property
    def has_access_rules(self):
        return bool(self.access_days or self.access_start_time or self.access_end_time)

    @property
    def access_summary(self):
        parts = []
        days = sorted(self.allowed_weekdays)
        if days and len(days) < 7:
            parts.append(", ".join(WEEKDAY_NAMES[str(d)] for d in days))
        if self.access_start_time and self.access_end_time:
            parts.append(f"{self.access_start_time:%H:%M}–{self.access_end_time:%H:%M}")
        return " · ".join(parts) or "Any day, any time"

    def access_denial_reason(self, moment):
        """Why access is not allowed at local datetime ``moment``, or None."""
        days = self.allowed_weekdays
        if days and moment.weekday() not in days:
            return f"This plan allows access only on {self.access_summary.split(' · ')[0]}."
        if self.access_start_time and self.access_end_time:
            now = moment.time()
            if not (self.access_start_time <= now <= self.access_end_time):
                return (f"This plan allows access only between {self.access_start_time:%H:%M} "
                        f"and {self.access_end_time:%H:%M}.")
        return None


ACCESS_WINDOW_CONSTRAINT = (
    Q(access_start_time__isnull=True, access_end_time__isnull=True)
    | Q(access_start_time__isnull=False, access_end_time__isnull=False, access_start_time__lt=F("access_end_time"))
)


class MembershipPlanQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class MembershipPlan(AccessRulesMixin, models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    duration_value = models.PositiveSmallIntegerField("duration")
    duration_unit = models.CharField(max_length=5, choices=DurationUnit.choices, default=DurationUnit.MONTH)
    price = models.DecimalField("price (LKR)", max_digits=10, decimal_places=2)
    benefits = models.TextField(blank=True, help_text="One benefit per line.")
    access_restrictions = models.TextField(
        blank=True, help_text="Optional, e.g. 'Off-peak hours only (6am–4pm)' or 'Gym floor only, no classes'."
    )
    is_active = models.BooleanField("active", default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MembershipPlanQuerySet.as_manager()

    class Meta:
        ordering = ["-is_active", "price", "name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="plan_unique_name_ci"),
            models.CheckConstraint(condition=Q(price__gte=0), name="plan_price_not_negative"),
            models.CheckConstraint(
                condition=Q(duration_unit=periods.MONTH, duration_value__gte=1, duration_value__lte=MAX_DURATION_MONTHS)
                | Q(duration_unit=periods.DAY, duration_value__gte=1, duration_value__lte=MAX_DURATION_DAYS),
                name="plan_duration_valid",
            ),
            models.CheckConstraint(condition=ACCESS_WINDOW_CONSTRAINT, name="plan_access_window_valid"),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("memberships:plan_detail", args=[self.pk])

    @property
    def duration_display(self):
        return periods.describe(self.duration_value, self.duration_unit)

    @property
    def duration_preset(self):
        for key, (_label, value, unit) in DURATION_PRESETS.items():
            if (value, unit) == (self.duration_value, self.duration_unit):
                return key
        return "custom"

    @property
    def benefit_list(self):
        return [line.strip(" -•\t") for line in self.benefits.splitlines() if line.strip(" -•\t")]


class MembershipStatus(models.TextChoices):
    """Effective status shown to users (derived from state and dates)."""

    SCHEDULED = "scheduled", "Scheduled"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


STATUS_BADGES = {
    MembershipStatus.SCHEDULED: "info",
    MembershipStatus.ACTIVE: "success",
    MembershipStatus.SUSPENDED: "warning",
    MembershipStatus.EXPIRED: "danger",
    MembershipStatus.CANCELLED: "secondary",
}


class MembershipQuerySet(models.QuerySet):
    def for_member(self, member):
        return self.filter(member=member)

    def not_cancelled(self):
        return self.exclude(state=Membership.State.CANCELLED)

    def open(self, today=None):
        """Current or scheduled (not yet ended, not cancelled)."""
        today = today or timezone.localdate()
        return self.not_cancelled().filter(end_date__gte=today)

    def current(self, today=None):
        today = today or timezone.localdate()
        return self.not_cancelled().filter(start_date__lte=today, end_date__gte=today)

    def _later_period_exists(self):
        return Exists(
            Membership.objects.not_cancelled().filter(member=OuterRef("member"), start_date__gt=OuterRef("end_date"))
        )

    def with_status(self, status, today=None):
        today = today or timezone.localdate()
        if status == MembershipStatus.CANCELLED:
            return self.filter(state=Membership.State.CANCELLED)
        qs = self.not_cancelled()
        if status == MembershipStatus.EXPIRED:
            return qs.filter(end_date__lt=today)
        if status == MembershipStatus.SCHEDULED:
            return qs.filter(start_date__gt=today)
        qs = qs.filter(start_date__lte=today, end_date__gte=today)
        if status == MembershipStatus.SUSPENDED:
            return qs.filter(state=Membership.State.SUSPENDED)
        if status == MembershipStatus.ACTIVE:
            return qs.filter(state=Membership.State.ACTIVE)
        return self.none()

    def expiring_soon(self, days=None, today=None):
        """Current memberships ending within ``days`` that haven't been renewed."""
        today = today or timezone.localdate()
        days = gym.expiring_soon_days() if days is None else days
        return (
            self.current(today)
            .filter(end_date__lte=today + timedelta(days=days))
            .filter(~self._later_period_exists())
        )

    def lapsed(self, today=None):
        """Expired memberships that were not followed by a renewal."""
        today = today or timezone.localdate()
        return self.not_cancelled().filter(end_date__lt=today).filter(~self._later_period_exists())


class Membership(AccessRulesMixin, models.Model):
    """
    One membership period. Plan name, duration and price are copied from the
    plan at assignment so later plan edits never change past agreements.
    Renewals create a new period; existing periods are never overwritten.
    """

    class State(models.TextChoices):
        ACTIVE = "active", "In good standing"
        SUSPENDED = "suspended", "Suspended"
        CANCELLED = "cancelled", "Cancelled"

    class Kind(models.TextChoices):
        NEW = "new", "New"
        RENEWAL = "renewal", "Renewal"
        PLAN_CHANGE = "plan_change", "Plan change"

    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="memberships")
    plan = models.ForeignKey(MembershipPlan, on_delete=models.PROTECT, related_name="memberships")
    # The period this one renews (null for a first membership).
    previous = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="following_periods"
    )
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.NEW)

    # Snapshot of the agreement at assignment time (including access rules).
    plan_name = models.CharField(max_length=100)
    duration_value = models.PositiveSmallIntegerField()
    duration_unit = models.CharField(max_length=5, choices=DurationUnit.choices)
    price = models.DecimalField("agreed price (LKR)", max_digits=10, decimal_places=2)

    start_date = models.DateField(db_index=True)
    end_date = models.DateField(db_index=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE, db_index=True)
    suspended_on = models.DateField(null=True, blank=True)
    cancelled_on = models.DateField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MembershipQuerySet.as_manager()

    class Meta:
        ordering = ["-start_date", "-pk"]
        indexes = [models.Index(fields=["member", "start_date", "end_date"], name="membership_member_period")]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="membership_end_after_start"),
            models.CheckConstraint(condition=ACCESS_WINDOW_CONSTRAINT, name="membership_access_window_valid"),
            models.CheckConstraint(condition=Q(price__gte=0), name="membership_price_not_negative"),
            models.CheckConstraint(condition=Q(duration_value__gte=1), name="membership_duration_positive"),
            models.CheckConstraint(
                condition=(Q(state="cancelled") & Q(cancelled_on__isnull=False))
                | (~Q(state="cancelled") & Q(cancelled_on__isnull=True)),
                name="membership_cancelled_has_date",
            ),
            models.CheckConstraint(
                condition=(Q(state="suspended") & Q(suspended_on__isnull=False))
                | (~Q(state="suspended") & Q(suspended_on__isnull=True)),
                name="membership_suspended_has_date",
            ),
        ]

    def __str__(self):
        return f"{self.plan_name}: {self.start_date:%d %b %Y} – {self.end_date:%d %b %Y}"

    def get_absolute_url(self):
        return reverse("memberships:detail", args=[self.pk])

    def get_status(self, today=None):
        today = today or timezone.localdate()
        if self.state == self.State.CANCELLED:
            return MembershipStatus.CANCELLED
        if today > self.end_date:
            return MembershipStatus.EXPIRED
        if today < self.start_date:
            return MembershipStatus.SCHEDULED
        if self.state == self.State.SUSPENDED:
            return MembershipStatus.SUSPENDED
        return MembershipStatus.ACTIVE

    @property
    def status(self):
        return self.get_status()

    @property
    def status_label(self):
        return MembershipStatus(self.status).label

    @property
    def status_badge(self):
        return STATUS_BADGES[self.status]

    @property
    def duration_display(self):
        return periods.describe(self.duration_value, self.duration_unit)

    @property
    def total_days(self):
        return (self.end_date - self.start_date).days + 1

    @property
    def days_remaining(self):
        today = timezone.localdate()
        if self.status in (MembershipStatus.EXPIRED, MembershipStatus.CANCELLED):
            return 0
        return (self.end_date - max(today, self.start_date)).days + 1

    @property
    def can_suspend(self):
        return self.status == MembershipStatus.ACTIVE

    @property
    def can_reactivate(self):
        return self.status == MembershipStatus.SUSPENDED

    @property
    def can_cancel(self):
        return self.status in (MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED, MembershipStatus.SCHEDULED)


class MembershipEvent(models.Model):
    """Audit trail of lifecycle changes for one membership period."""

    class Type(models.TextChoices):
        ASSIGNED = "assigned", "Assigned"
        RENEWED = "renewed", "Renewed"
        PLAN_CHANGED = "plan_changed", "Plan change scheduled"
        SUSPENDED = "suspended", "Suspended"
        REACTIVATED = "reactivated", "Reactivated"
        CANCELLED = "cancelled", "Cancelled"

    STYLES = {
        "assigned": ("card-checklist", "success"),
        "renewed": ("arrow-repeat", "success"),
        "plan_changed": ("arrow-left-right", "primary"),
        "suspended": ("pause-circle", "warning"),
        "reactivated": ("play-circle", "success"),
        "cancelled": ("x-circle", "danger"),
    }

    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=15, choices=Type.choices)
    note = models.CharField(max_length=255, blank=True)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.get_event_type_display()} – {self.membership}"

    @property
    def icon(self):
        return self.STYLES.get(self.event_type, ("activity", "secondary"))[0]

    @property
    def color(self):
        return self.STYLES.get(self.event_type, ("activity", "secondary"))[1]
