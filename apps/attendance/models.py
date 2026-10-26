import secrets

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


def new_pass_token():
    # 192 bits of randomness, URL-safe. Contains no member information.
    return secrets.token_urlsafe(24)


class CheckInPass(models.Model):
    """The secret behind a member's check-in QR code. Reissuing invalidates the old one."""

    member = models.OneToOneField("members.Member", on_delete=models.CASCADE, related_name="checkin_pass")
    token = models.CharField(max_length=64, unique=True, default=new_pass_token, editable=False)
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    def __str__(self):
        return f"Check-in pass for {self.member.member_id}"

    @property
    def qr_payload(self):
        return f"{settings.ATTENDANCE_QR_PREFIX}:{self.token}"


class Attendance(models.Model):
    """One gym visit: a check-in and (normally) a check-out."""

    class Method(models.TextChoices):
        MANUAL = "manual", "Manual"
        QR = "qr", "QR code"

    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="visits")
    membership = models.ForeignKey(
        "memberships.Membership", on_delete=models.PROTECT, related_name="visits",
        help_text="The membership that authorised entry.",
    )
    checked_in_at = models.DateTimeField(default=timezone.now, db_index=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)
    # Set when a visit from an earlier day was never checked out.
    auto_closed = models.BooleanField("no check-out recorded", default=False)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.MANUAL)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    checked_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Outstanding membership balance when the member entered (for follow-up).
    balance_due_at_checkin = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["-checked_in_at", "-pk"]
        indexes = [models.Index(fields=["member", "checked_in_at"], name="attendance_member_time")]
        constraints = [
            # At most one open visit per member: the database rejects a duplicate
            # check-in even if two requests arrive at the same moment.
            models.UniqueConstraint(
                fields=["member"], condition=Q(checked_out_at__isnull=True, auto_closed=False),
                name="attendance_one_open_visit",
            ),
            models.CheckConstraint(
                condition=Q(checked_out_at__isnull=True) | Q(checked_out_at__gte=F("checked_in_at")),
                name="attendance_checkout_after_checkin",
            ),
            models.CheckConstraint(
                condition=~Q(auto_closed=True) | Q(checked_out_at__isnull=True),
                name="attendance_auto_closed_has_no_checkout",
            ),
        ]

    def __str__(self):
        return f"{self.member} @ {timezone.localtime(self.checked_in_at):%d %b %Y %H:%M}"

    @property
    def is_open(self):
        return self.checked_out_at is None and not self.auto_closed

    @property
    def status_label(self):
        if self.is_open:
            return "In gym"
        return "No check-out" if self.auto_closed else "Checked out"

    @property
    def status_badge(self):
        if self.is_open:
            return "success"
        return "warning" if self.auto_closed else "secondary"

    @property
    def duration(self):
        if self.checked_out_at:
            return self.checked_out_at - self.checked_in_at
        if self.is_open:
            return timezone.now() - self.checked_in_at
        return None

    @property
    def duration_display(self):
        d = self.duration
        if d is None:
            return "—"
        minutes = int(d.total_seconds() // 60)
        return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes} min"
