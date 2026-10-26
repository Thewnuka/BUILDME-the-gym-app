"""
Balance and payment-status rules. Everything is derived from the ledger.

    charge      = agreed price + surcharges - discounts
    net paid    = completed payments - completed refunds
    outstanding = charge - net paid (never below zero)

Pending payments (e.g. unconfirmed bank transfers) are not collected money:
they don't reduce the outstanding balance, but they do count when checking
for overpayment so the same debt can't be paid twice while one is pending.

A chargeable item is a membership (charge = agreed price ± adjustments) or a
training session (charge = the fee fixed at booking).

Cancelled memberships and sessions (approved policy): the charge stops at cancellation.
Their effective charge becomes what was already paid, so they never show as
outstanding. Refunding paid money is a separate, authorised decision.
"""
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Case, DecimalField, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce, Greatest

from apps.memberships.models import Membership

from .models import BalanceAdjustment, Payment

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=12, decimal_places=2)


class PaymentStatus:
    UNPAID = "unpaid"
    PENDING = "pending"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    NO_CHARGE = "no_charge"

    LABELS = {
        UNPAID: "Unpaid", PENDING: "Pending", PARTIALLY_PAID: "Partially paid", PAID: "Paid",
        PARTIALLY_REFUNDED: "Partially refunded", REFUNDED: "Refunded", NO_CHARGE: "No charge",
    }
    BADGES = {
        UNPAID: "danger", PENDING: "warning", PARTIALLY_PAID: "warning", PAID: "success",
        PARTIALLY_REFUNDED: "info", REFUNDED: "secondary", NO_CHARGE: "secondary",
    }


@dataclass(frozen=True)
class Balance:
    price: Decimal
    adjustments: Decimal
    paid: Decimal
    refunded: Decimal
    pending: Decimal
    cancelled: bool

    @property
    def charge(self):
        """What the member owes for this membership."""
        base = self.price + self.adjustments
        if self.cancelled:
            return min(base, max(self.net_paid, ZERO))
        return base

    @property
    def net_paid(self):
        return self.paid - self.refunded

    @property
    def outstanding(self):
        return max(self.charge - self.net_paid, ZERO)

    @property
    def payable(self):
        """Maximum new payment that may be recorded now."""
        return max(self.charge - self.net_paid - self.pending, ZERO)

    @property
    def status(self):
        if self.refunded > 0:
            return PaymentStatus.REFUNDED if self.net_paid <= 0 else PaymentStatus.PARTIALLY_REFUNDED
        if self.charge <= 0:
            return PaymentStatus.NO_CHARGE
        if self.net_paid >= self.charge:
            return PaymentStatus.PAID
        if self.net_paid > 0:
            return PaymentStatus.PARTIALLY_PAID
        if self.pending > 0:
            return PaymentStatus.PENDING
        return PaymentStatus.UNPAID

    @property
    def status_label(self):
        return PaymentStatus.LABELS[self.status]

    @property
    def status_badge(self):
        return PaymentStatus.BADGES[self.status]


def _sum(qs, field="amount"):
    return qs.aggregate(total=Coalesce(Sum(field), ZERO, output_field=MONEY))["total"]


def is_appointment(target):
    return target._meta.label == "appointments.Appointment"


def target_filter(target):
    return {"appointment": target} if is_appointment(target) else {"membership": target}


def is_cancelled(target):
    if is_appointment(target):
        return target.status == "cancelled"
    return target.state == Membership.State.CANCELLED


def balance_for(target):
    """Balance of a membership or a training session."""
    payments = Payment.objects.filter(**target_filter(target))
    if is_appointment(target):
        price, adjustments = target.fee, ZERO
    else:
        adj = BalanceAdjustment.objects.filter(membership=target)
        price = target.price
        adjustments = (_sum(adj.filter(kind=BalanceAdjustment.Kind.SURCHARGE))
                       - _sum(adj.filter(kind=BalanceAdjustment.Kind.DISCOUNT)))
    return Balance(
        price=price,
        adjustments=adjustments,
        paid=_sum(payments.filter(kind=Payment.Kind.PAYMENT, status=Payment.Status.COMPLETED)),
        refunded=_sum(payments.filter(kind=Payment.Kind.REFUND, status=Payment.Status.COMPLETED)),
        pending=_sum(payments.filter(kind=Payment.Kind.PAYMENT, status=Payment.Status.PENDING)),
        cancelled=is_cancelled(target),
    )


def _subtotal(model, link="membership", **filters):
    qs = (
        model.objects.filter(**{link: OuterRef("pk")}, **filters)
        .order_by().values(link).annotate(total=Sum("amount")).values("total")
    )
    return Coalesce(Subquery(qs, output_field=MONEY), Value(ZERO), output_field=MONEY)


def with_balances(queryset=None):
    """Annotate memberships with ledger totals and ``outstanding`` (DB-side)."""
    qs = queryset if queryset is not None else Membership.objects.all()
    qs = qs.annotate(
        b_paid=_subtotal(Payment, kind=Payment.Kind.PAYMENT, status=Payment.Status.COMPLETED),
        b_refunded=_subtotal(Payment, kind=Payment.Kind.REFUND, status=Payment.Status.COMPLETED),
        b_pending=_subtotal(Payment, kind=Payment.Kind.PAYMENT, status=Payment.Status.PENDING),
        b_surcharge=_subtotal(BalanceAdjustment, kind=BalanceAdjustment.Kind.SURCHARGE),
        b_discount=_subtotal(BalanceAdjustment, kind=BalanceAdjustment.Kind.DISCOUNT),
    )
    charge = ExpressionWrapper(F("price") + F("b_surcharge") - F("b_discount"), output_field=MONEY)
    net = ExpressionWrapper(F("b_paid") - F("b_refunded"), output_field=MONEY)
    return qs.annotate(
        b_charge=charge,
        b_net_paid=net,
        outstanding=Case(
            When(Q(state=Membership.State.CANCELLED), then=Value(ZERO)),
            default=Greatest(
                ExpressionWrapper(charge - net, output_field=MONEY), Value(ZERO), output_field=MONEY
            ),
            output_field=MONEY,
        ),
    )


def with_appointment_balances(queryset):
    """Annotate training sessions with ``outstanding`` fees (DB-side)."""
    qs = queryset.annotate(
        b_paid=_subtotal(Payment, "appointment", kind=Payment.Kind.PAYMENT, status=Payment.Status.COMPLETED),
        b_refunded=_subtotal(Payment, "appointment", kind=Payment.Kind.REFUND, status=Payment.Status.COMPLETED),
        b_pending=_subtotal(Payment, "appointment", kind=Payment.Kind.PAYMENT, status=Payment.Status.PENDING),
    )
    net = ExpressionWrapper(F("b_paid") - F("b_refunded"), output_field=MONEY)
    return qs.annotate(
        b_net_paid=net,
        outstanding=Case(
            When(Q(status="cancelled"), then=Value(ZERO)),
            default=Greatest(ExpressionWrapper(F("fee") - net, output_field=MONEY), Value(ZERO), output_field=MONEY),
            output_field=MONEY,
        ),
    )


def balance_from_annotations(membership):
    """Build a Balance from a membership annotated by ``with_balances``."""
    return Balance(
        price=membership.price, adjustments=membership.b_surcharge - membership.b_discount,
        paid=membership.b_paid, refunded=membership.b_refunded, pending=membership.b_pending,
        cancelled=membership.state == Membership.State.CANCELLED,
    )


def refundable_amount(payment):
    """How much of a completed payment can still be refunded."""
    if payment.kind != Payment.Kind.PAYMENT or payment.status != Payment.Status.COMPLETED:
        return ZERO
    refunded = _sum(payment.refunds.filter(status=Payment.Status.COMPLETED))
    return max(payment.amount - refunded, ZERO)
