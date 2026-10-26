from django.conf import settings
from django.db import models


class ActivityLog(models.Model):
    """Audit trail of notable actions, shown as "Recent activities"."""

    class Action(models.TextChoices):
        LOGIN = "login", "Signed in"
        LOGOUT = "logout", "Signed out"
        LOGIN_FAILED = "login_failed", "Failed sign-in"
        USER_CREATED = "user_created", "User created"
        USER_REGISTERED = "user_registered", "Account registered"
        USER_UPDATED = "user_updated", "User updated"
        USER_ACTIVATED = "user_activated", "User activated"
        USER_DEACTIVATED = "user_deactivated", "User deactivated"
        ROLE_CHANGED = "role_changed", "Role changed"
        PASSWORD_CHANGED = "password_changed", "Password changed"
        PASSWORD_RESET = "password_reset", "Password reset"
        PERMISSIONS_UPDATED = "permissions_updated", "Permissions updated"
        PROFILE_UPDATED = "profile_updated", "Profile updated"
        MEMBER_REGISTERED = "member_registered", "Member registered"
        MEMBER_UPDATED = "member_updated", "Member updated"
        MEMBER_DEACTIVATED = "member_deactivated", "Member deactivated"
        MEMBER_REACTIVATED = "member_reactivated", "Member reactivated"
        PLAN_CREATED = "plan_created", "Plan created"
        PLAN_UPDATED = "plan_updated", "Plan updated"
        MEMBERSHIP_ASSIGNED = "membership_assigned", "Membership assigned"
        MEMBERSHIP_RENEWED = "membership_renewed", "Membership renewed"
        MEMBERSHIP_PLAN_CHANGED = "membership_plan_changed", "Plan change scheduled"
        MEMBERSHIP_SUSPENDED = "membership_suspended", "Membership suspended"
        MEMBERSHIP_REACTIVATED = "membership_reactivated", "Membership reactivated"
        MEMBERSHIP_CANCELLED = "membership_cancelled", "Membership cancelled"
        PAYMENT_RECORDED = "payment_recorded", "Payment recorded"
        PAYMENT_CONFIRMED = "payment_confirmed", "Payment confirmed"
        PAYMENT_VOIDED = "payment_voided", "Payment voided"
        REFUND_RECORDED = "refund_recorded", "Refund recorded"
        BALANCE_ADJUSTED = "balance_adjusted", "Balance adjusted"
        CHECKIN_PASS_REISSUED = "checkin_pass_reissued", "Check-in pass reissued"
        CHECKIN_REJECTED = "checkin_rejected", "Check-in rejected"
        TRAINER_UPDATED = "trainer_updated", "Trainer updated"
        TRAINER_ASSIGNED = "trainer_assigned", "Trainer assigned"
        TRAINER_UNASSIGNED = "trainer_unassigned", "Trainer assignment ended"
        TRAINER_PAY_RECORDED = "trainer_pay_recorded", "Trainer pay recorded"
        APPOINTMENT_BOOKED = "appointment_booked", "Session booked"
        APPOINTMENT_RESCHEDULED = "appointment_rescheduled", "Session rescheduled"
        APPOINTMENT_CANCELLED = "appointment_cancelled", "Session cancelled"
        APPOINTMENT_COMPLETED = "appointment_completed", "Session completed"
        WORKOUT_PLAN_ASSIGNED = "workout_plan_assigned", "Workout plan assigned"
        MEAL_PLAN_ASSIGNED = "meal_plan_assigned", "Meal plan assigned"
        EXPENSE_RECORDED = "expense_recorded", "Expense recorded"
        EXPENSE_VOIDED = "expense_voided", "Expense voided"
        EQUIPMENT_UPDATED = "equipment_updated", "Equipment updated"
        EQUIPMENT_FAULT = "equipment_fault", "Equipment fault reported"
        EQUIPMENT_MAINTAINED = "equipment_maintained", "Maintenance recorded"
        STOCK_MOVEMENT = "stock_movement", "Stock movement"
        ANNOUNCEMENT_PUBLISHED = "announcement_published", "Announcement published"
        SETTINGS_UPDATED = "settings_updated", "Gym settings updated"

    ACTION_STYLES = {
        "login": ("box-arrow-in-right", "success"),
        "logout": ("box-arrow-right", "secondary"),
        "login_failed": ("shield-exclamation", "danger"),
        "user_created": ("person-plus", "primary"),
        "user_registered": ("person-plus-fill", "success"),
        "user_updated": ("pencil-square", "info"),
        "user_activated": ("person-check", "success"),
        "user_deactivated": ("person-slash", "warning"),
        "role_changed": ("person-badge", "primary"),
        "password_changed": ("key", "info"),
        "password_reset": ("key", "warning"),
        "permissions_updated": ("shield-lock", "danger"),
        "profile_updated": ("person-gear", "info"),
        "member_registered": ("person-plus-fill", "success"),
        "member_updated": ("person-lines-fill", "info"),
        "member_deactivated": ("person-dash", "warning"),
        "member_reactivated": ("person-check-fill", "success"),
        "plan_created": ("card-checklist", "primary"),
        "plan_updated": ("card-checklist", "info"),
        "membership_assigned": ("card-checklist", "success"),
        "membership_renewed": ("arrow-repeat", "success"),
        "membership_plan_changed": ("arrow-left-right", "primary"),
        "membership_suspended": ("pause-circle", "warning"),
        "membership_reactivated": ("play-circle", "success"),
        "membership_cancelled": ("x-circle", "danger"),
        "payment_recorded": ("cash-coin", "success"),
        "payment_confirmed": ("check2-circle", "success"),
        "payment_voided": ("slash-circle", "danger"),
        "refund_recorded": ("arrow-counterclockwise", "warning"),
        "balance_adjusted": ("sliders", "info"),
        "checkin_pass_reissued": ("qr-code", "primary"),
        "checkin_rejected": ("shield-exclamation", "danger"),
        "trainer_updated": ("person-badge", "info"),
        "trainer_assigned": ("person-plus", "primary"),
        "trainer_unassigned": ("person-dash", "secondary"),
        "trainer_pay_recorded": ("wallet2", "warning"),
        "appointment_booked": ("calendar-plus", "success"),
        "appointment_rescheduled": ("calendar-event", "info"),
        "appointment_cancelled": ("calendar-x", "danger"),
        "appointment_completed": ("calendar-check", "success"),
        "workout_plan_assigned": ("lightning-charge", "primary"),
        "meal_plan_assigned": ("egg-fried", "primary"),
        "expense_recorded": ("receipt-cutoff", "warning"),
        "expense_voided": ("slash-circle", "danger"),
        "equipment_updated": ("tools", "info"),
        "equipment_fault": ("exclamation-triangle", "danger"),
        "equipment_maintained": ("wrench", "success"),
        "stock_movement": ("box-seam", "info"),
        "announcement_published": ("megaphone", "primary"),
    }

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="activities"
    )
    action = models.CharField(max_length=40, choices=Action.choices, db_index=True)
    description = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.get_action_display()}: {self.description}"

    @property
    def icon(self):
        return self.ACTION_STYLES.get(self.action, ("activity", "secondary"))[0]

    @property
    def color(self):
        return self.ACTION_STYLES.get(self.action, ("activity", "secondary"))[1]


class Notification(models.Model):
    """
    An in-app notification for a user, optionally also sent by email/SMS/WhatsApp
    (see NotificationDelivery). ``dedupe_key`` makes reminders idempotent: the
    database refuses a second notification with the same key.
    """

    class Category(models.TextChoices):
        MEMBERSHIP = "membership", "Membership"
        PAYMENT = "payment", "Payment"
        APPOINTMENT = "appointment", "Training session"
        INVENTORY = "inventory", "Inventory"
        EQUIPMENT = "equipment", "Equipment"
        ANNOUNCEMENT = "announcement", "Announcement"
        SYSTEM = "system", "System"

    ICONS = {
        "membership": "card-checklist", "payment": "cash-coin", "appointment": "calendar-event",
        "inventory": "box-seam", "equipment": "tools", "announcement": "megaphone", "system": "bell",
    }

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
                             related_name="notifications")
    member = models.ForeignKey("members.Member", null=True, blank=True, on_delete=models.CASCADE,
                               related_name="notifications",
                               help_text="Set for member-facing messages (also members without a login).")
    category = models.CharField(max_length=12, choices=Category.choices, default=Category.SYSTEM, db_index=True)
    title = models.CharField(max_length=120)
    message = models.CharField(max_length=500, blank=True)
    link = models.CharField(max_length=200, blank=True)
    dedupe_key = models.CharField(max_length=150, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["user", "read_at"], name="notification_user_unread")]
        constraints = [
            models.CheckConstraint(condition=models.Q(user__isnull=False) | models.Q(member__isnull=False),
                                   name="notification_has_recipient"),
        ]

    def __str__(self):
        return f"{self.user or self.member}: {self.title}"

    @property
    def is_read(self):
        return self.read_at is not None

    @property
    def icon(self):
        return self.ICONS.get(self.category, "bell")


class NotificationDelivery(models.Model):
    """
    One attempt to deliver a notification through an external channel.

    Status meanings (never optimistic):
      skipped   – not attempted (channel not configured / no address / dev backend)
      queued    – accepted by the provider for sending; outcome not yet known
      sent      – handed over (email accepted by the mail server, or SMS accepted
                  by a provider that gives no delivery receipts)
      delivered – the provider confirmed delivery to the handset
      failed    – the provider or mail server rejected it
    """

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"

    class Status(models.TextChoices):
        SKIPPED = "skipped", "Not sent"
        QUEUED = "queued", "Queued at provider"
        SENT = "sent", "Sent (no delivery receipt)"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    STATUS_BADGES = {"skipped": "secondary", "queued": "info", "sent": "primary", "delivered": "success",
                     "failed": "danger"}

    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.CharField(max_length=10, choices=Channel.choices)
    recipient = models.CharField(max_length=254, blank=True)
    provider = models.CharField(max_length=20, blank=True)
    provider_message_id = models.CharField(max_length=100, blank=True, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SKIPPED, db_index=True)
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.get_channel_display()} → {self.recipient}: {self.get_status_display()}"

    @property
    def status_badge(self):
        return self.STATUS_BADGES.get(self.status, "secondary")


class GymSettings(models.Model):
    """Single-row business settings editable in the app.

    Every field is optional: blank means "use the server default" from the
    environment (.env), so behaviour is unchanged until someone edits a value.
    Read through ``apps.core.gym`` (cached), never directly.
    """

    gym_name = models.CharField(max_length=80, blank=True, help_text="Shown in the header, receipts, reports and messages.")
    address = models.TextField(blank=True, max_length=300)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    business_registration = models.CharField("Business registration no.", max_length=60, blank=True)
    receipt_footer = models.CharField(max_length=200, blank=True,
                                      help_text="Printed at the bottom of every receipt, e.g. a thank-you or refund policy.")
    membership_expiry_reminder_days = models.CharField(
        max_length=40, blank=True, help_text="Days before expiry to remind members, comma-separated, e.g. 7,1.")
    payment_due_reminder_days = models.PositiveSmallIntegerField(null=True, blank=True)
    expiring_soon_days = models.PositiveSmallIntegerField("\"Expiring soon\" window (days)", null=True, blank=True)
    equipment_maintenance_reminder_days = models.PositiveSmallIntegerField(null=True, blank=True)
    appointment_reminder_hours = models.PositiveSmallIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        verbose_name = verbose_name_plural = "gym settings"
        constraints = [models.CheckConstraint(condition=models.Q(pk=1), name="gym_settings_singleton")]

    def __str__(self):
        return "Gym settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
