"""
Payment ledger.

Money that moves is a ``Payment`` row (kind ``payment`` = money in, ``refund``
= money out, both with a positive amount). Changes to what a member owes are
``BalanceAdjustment`` rows (discounts/waivers or surcharges). Rows are never
deleted or edited after the fact: mistakes are voided with a reason, and
adjustments are corrected with an opposite adjustment.

Balances and payment statuses are always *derived* from these rows (see
``balances.py``); nothing on a membership is marked "paid" directly.
"""
from django.conf import settings
from django.db import models, transaction
from django.db.models import F, Q
from django.urls import reverse


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    CARD = "card", "Card"


class Payment(models.Model):
    class Kind(models.TextChoices):
        PAYMENT = "payment", "Payment"
        REFUND = "refund", "Refund"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        VOIDED = "voided", "Voided"

    STATUS_BADGES = {"pending": "warning", "completed": "success", "voided": "secondary"}

    reference = models.CharField("transaction reference", max_length=20, unique=True, null=True, editable=False)
    receipt_number = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PAYMENT, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COMPLETED, db_index=True)

    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="payments")
    # A payment settles exactly one chargeable item: a membership or a training session.
    membership = models.ForeignKey("memberships.Membership", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="payments")
    appointment = models.ForeignKey("appointments.Appointment", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="payments")
    refund_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="refunds",
        help_text="The original payment this refund returns money from.",
    )

    amount = models.DecimalField("amount (LKR)", max_digits=10, decimal_places=2)
    method = models.CharField(max_length=15, choices=PaymentMethod.choices)
    paid_on = models.DateField("payment date", db_index=True)
    external_reference = models.CharField(
        "bank / card reference", max_length=64, blank=True,
        help_text="Bank transfer reference or card terminal approval code. Never a card number.",
    )
    notes = models.CharField(max_length=255, blank=True)

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=255, blank=True)

    # One-time token embedded in the form: a double-click or resubmitted form
    # hits this unique constraint instead of recording the payment twice.
    submission_token = models.UUIDField(unique=True, null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-recorded_at", "-pk"]
        indexes = [models.Index(fields=["membership", "kind", "status"], name="payment_membership_kind_status")]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
            models.CheckConstraint(
                condition=(Q(membership__isnull=False) & Q(appointment__isnull=True))
                | (Q(membership__isnull=True) & Q(appointment__isnull=False)),
                name="payment_single_target",
            ),
            models.CheckConstraint(
                condition=(Q(kind="refund") & Q(refund_of__isnull=False))
                | (Q(kind="payment") & Q(refund_of__isnull=True)),
                name="payment_refund_has_original",
            ),
            models.CheckConstraint(
                condition=~Q(status="voided") | (Q(voided_at__isnull=False) & ~Q(void_reason="")),
                name="payment_void_has_reason",
            ),
            models.CheckConstraint(
                condition=~Q(kind="refund") | ~Q(status="pending"), name="payment_refund_not_pending",
            ),
            models.CheckConstraint(
                condition=Q(receipt_number__isnull=True) | ~Q(status="pending"),
                name="payment_pending_has_no_receipt",
            ),
            models.CheckConstraint(
                condition=Q(confirmed_at__isnull=True) | Q(confirmed_at__gte=F("recorded_at")),
                name="payment_confirmed_after_recorded",
            ),
        ]

    def __str__(self):
        return f"{self.reference or 'new'} · {self.get_kind_display()} LKR {self.amount:,.2f}"

    def get_absolute_url(self):
        return reverse("payments:detail", args=[self.pk])

    @property
    def target(self):
        """The membership or training session this transaction belongs to."""
        return self.membership or self.appointment

    @property
    def item_label(self):
        if self.appointment_id:
            a = self.appointment
            return f"{a.session_type.name} with {a.trainer.name}"
        return f"{self.membership.plan_name} membership"

    @property
    def item_detail(self):
        from django.utils import timezone

        if self.appointment_id:
            return f"Session on {timezone.localtime(self.appointment.starts_at):%d %b %Y, %H:%M}"
        m = self.membership
        return f"{m.start_date:%d %b %Y} – {m.end_date:%d %b %Y}"

    @property
    def is_refund(self):
        return self.kind == self.Kind.REFUND

    @property
    def status_badge(self):
        return self.STATUS_BADGES.get(self.status, "secondary")

    @property
    def signed_amount(self):
        return -self.amount if self.is_refund else self.amount

    def assign_numbers(self):
        """Give a saved row its reference (always) and receipt number (once completed)."""
        updates = {}
        if not self.reference:
            self.reference = updates["reference"] = f"TXN-{self.pk:07d}"
        if self.status == self.Status.COMPLETED and not self.receipt_number:
            prefix = "RFD" if self.is_refund else "RCPT"
            self.receipt_number = updates["receipt_number"] = f"{prefix}-{self.pk:07d}"
        if updates:
            type(self).objects.filter(pk=self.pk).update(**updates)

    def save(self, *args, **kwargs):
        # Numbers derive from the primary key, so they can never collide.
        with transaction.atomic():
            super().save(*args, **kwargs)
            self.assign_numbers()


class BalanceAdjustment(models.Model):
    """An authorised change to what a member owes for one membership."""

    class Kind(models.TextChoices):
        DISCOUNT = "discount", "Discount / waiver"
        SURCHARGE = "surcharge", "Surcharge"

    membership = models.ForeignKey("memberships.Membership", on_delete=models.PROTECT, related_name="adjustments")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.DecimalField("amount (LKR)", max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=255)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="adjustment_amount_positive"),
            models.CheckConstraint(condition=~Q(reason=""), name="adjustment_has_reason"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} LKR {self.amount:,.2f}"

    @property
    def signed_amount(self):
        return -self.amount if self.kind == self.Kind.DISCOUNT else self.amount
