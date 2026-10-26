from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role


class AnnouncementQuerySet(models.QuerySet):
    def live(self, now=None):
        now = now or timezone.now()
        return self.filter(is_active=True, publish_at__lte=now).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


class Announcement(models.Model):
    class Kind(models.TextChoices):
        GENERAL = "general", "General news"
        CLOSURE = "closure", "Gym closure"
        OPERATIONAL = "operational", "Operational update"
        EVENT = "event", "Event / promotion"

    class Audience(models.TextChoices):
        EVERYONE = "everyone", "Everyone (members and staff)"
        MEMBERS = "members", "All active members"
        ACTIVE_MEMBERSHIP = "active_membership", "Members with a current membership"
        PLAN = "plan", "Members on a specific plan"
        STAFF = "staff", "All staff"
        ROLE = "role", "One staff role"

    KIND_BADGES = {"general": "info", "closure": "danger", "operational": "warning", "event": "success"}

    title = models.CharField(max_length=150)
    body = models.TextField()
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.GENERAL)
    audience = models.CharField(max_length=20, choices=Audience.choices, default=Audience.EVERYONE)
    plan = models.ForeignKey("memberships.MembershipPlan", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")
    role = models.CharField(max_length=20, choices=[(r.value, r.label) for r in Role if r != Role.MEMBER],
                            blank=True)
    publish_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    notify_recipients = models.BooleanField("notify recipients", default=True,
                                            help_text="Send an in-app notification when it is published.")
    send_external = models.BooleanField("also email/SMS/WhatsApp members", default=False,
                                        help_text="Uses the channels configured in NOTIFY_CHANNELS.")
    notified_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AnnouncementQuerySet.as_manager()

    class Meta:
        ordering = ["-publish_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(expires_at__isnull=True) | Q(expires_at__gt=F("publish_at")),
                                   name="announcement_expiry_after_publish"),
            models.CheckConstraint(condition=~Q(audience="plan") | Q(plan__isnull=False),
                                   name="announcement_plan_audience_has_plan"),
            models.CheckConstraint(condition=~Q(audience="role") | ~Q(role=""),
                                   name="announcement_role_audience_has_role"),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("announcements:detail", args=[self.pk])

    @property
    def kind_badge(self):
        return self.KIND_BADGES.get(self.kind, "secondary")

    @property
    def state(self):
        now = timezone.now()
        if not self.is_active:
            return "Inactive"
        if self.publish_at > now:
            return "Scheduled"
        if self.expires_at and self.expires_at <= now:
            return "Expired"
        return "Live"

    @property
    def audience_label(self):
        if self.audience == self.Audience.PLAN and self.plan_id:
            return f"Members on {self.plan.name}"
        if self.audience == self.Audience.ROLE and self.role:
            return f"Staff: {Role(self.role).label}"
        return self.get_audience_display()
