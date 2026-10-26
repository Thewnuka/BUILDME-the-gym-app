"""
Payment operations. Each runs in a transaction and locks the chargeable row
(membership or training session) first, so simultaneous submissions for the
same item are serialised and can't overpay or over-refund it.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.core.utils import log_activity
from apps.memberships.models import Membership

from .balances import balance_for, is_appointment, refundable_amount
from .models import BalanceAdjustment, Payment, PaymentMethod

Action = ActivityLog.Action
MAX_AMOUNT = Decimal("9999999.99")
CARD_NUMBER_RE = re.compile(r"(?:\d[ -]?){13,19}")


class PaymentError(Exception):
    """A payment operation violates a business rule."""


def looks_like_card_number(text):
    """True if ``text`` contains a 13–19 digit sequence passing the Luhn check."""
    for match in CARD_NUMBER_RE.finditer(text or ""):
        digits = [int(c) for c in match.group() if c.isdigit()]
        checksum = 0
        for i, d in enumerate(reversed(digits)):
            if i % 2:
                d *= 2
                d = d - 9 if d > 9 else d
            checksum += d
        if checksum % 10 == 0:
            return True
    return False


def _notify_payment_received(payment):
    from apps.core.models import Notification
    from apps.core.notify import notify

    notify(member=payment.member, title="Payment received",
           message=f"LKR {payment.amount:,.2f} received for {payment.item_label}. Receipt {payment.receipt_number}.",
           link=f"/payments/{payment.pk}/receipt/", category=Notification.Category.PAYMENT,
           dedupe_key=f"payment_received:{payment.pk}", external=True)


def _check_amount(amount):
    if amount is None or amount <= 0:
        raise PaymentError("The amount must be greater than zero.")
    if amount > MAX_AMOUNT:
        raise PaymentError("The amount is too large.")
    if amount != amount.quantize(Decimal("0.01")):
        raise PaymentError("Amounts can have at most two decimal places.")


def _check_date(paid_on):
    today = timezone.localdate()
    if paid_on > today:
        raise PaymentError("The payment date can't be in the future.")
    if paid_on < today - timedelta(days=settings.PAYMENT_MAX_BACKDATE_DAYS):
        raise PaymentError(f"The payment date can't be more than {settings.PAYMENT_MAX_BACKDATE_DAYS} days ago.")


def _check_reference(text):
    if looks_like_card_number(text):
        raise PaymentError("Don't enter card numbers. Record only the terminal approval code or last 4 digits.")


def _lock_membership(membership):
    return Membership.objects.select_for_update().select_related("member").get(pk=membership.pk)


def _lock_target(target):
    if is_appointment(target):
        return type(target).objects.select_for_update().select_related("member").get(pk=target.pk)
    return _lock_membership(target)


def _target_fields(target):
    return {"appointment": target, "membership": None} if is_appointment(target) else {"membership": target}


def _existing_for_token(token):
    return Payment.objects.filter(submission_token=token).first() if token else None


@transaction.atomic
def _record(membership, amount, method, paid_on, actor, *, reference="", notes="", token=None, pending=False):
    membership = _lock_target(membership)  # a membership or a training session
    _check_amount(amount)
    _check_date(paid_on)
    _check_reference(reference)
    _check_reference(notes)
    if method not in PaymentMethod.values:
        raise PaymentError("Choose a valid payment method.")
    if pending and method != PaymentMethod.BANK_TRANSFER:
        raise PaymentError("Only bank transfers can be recorded as pending.")
    balance = balance_for(membership)
    if amount > balance.payable:
        if balance.payable <= 0:
            raise PaymentError("Nothing is payable on this item.")
        raise PaymentError(f"The amount exceeds the payable balance of LKR {balance.payable:,.2f}.")
    payment = Payment.objects.create(
        member=membership.member, **_target_fields(membership), kind=Payment.Kind.PAYMENT,
        status=Payment.Status.PENDING if pending else Payment.Status.COMPLETED,
        amount=amount, method=method, paid_on=paid_on, external_reference=reference.strip(),
        notes=notes.strip()[:255], recorded_by=actor, submission_token=token,
    )
    return payment


def record_payment(membership, amount, method, paid_on, actor, *, reference="", notes="", token=None,
                   pending=False, request=None):
    """
    Record a payment. Returns ``(payment, created)``. Submitting the same form
    token twice returns the first payment with ``created=False``.
    """
    existing = _existing_for_token(token)
    if existing:
        return existing, False
    try:
        payment = _record(membership, amount, method, paid_on, actor, reference=reference, notes=notes,
                          token=token, pending=pending)
    except IntegrityError:
        existing = _existing_for_token(token)
        if existing:  # a concurrent duplicate submission won the race
            return existing, False
        raise
    if not pending:
        _notify_payment_received(payment)
    state = "pending confirmation" if pending else "completed"
    log_activity(Action.PAYMENT_RECORDED,
                 f"{payment.reference}: LKR {amount:,.2f} {payment.get_method_display().lower()} from "
                 f"{payment.member.member_id} ({state})", actor=actor, request=request)
    return payment, True


@transaction.atomic
def confirm_payment(payment, actor, request=None):
    _lock_target(payment.target)
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status != Payment.Status.PENDING:
        raise PaymentError("Only pending payments can be confirmed.")
    payment.status = Payment.Status.COMPLETED
    payment.confirmed_by = actor
    payment.confirmed_at = timezone.now()
    payment.save(update_fields=["status", "confirmed_by", "confirmed_at"])
    log_activity(Action.PAYMENT_CONFIRMED, f"{payment.reference}: bank transfer confirmed", actor=actor, request=request)
    _notify_payment_received(payment)
    return payment


@transaction.atomic
def void_payment(payment, actor, reason, request=None):
    """Mark a mistaken entry as void. The row is kept for the audit trail."""
    _lock_target(payment.target)
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    reason = (reason or "").strip()
    if not reason:
        raise PaymentError("A reason is required to void a transaction.")
    if payment.status == Payment.Status.VOIDED:
        raise PaymentError("This transaction is already void.")
    if payment.kind == Payment.Kind.PAYMENT and payment.refunds.exclude(status=Payment.Status.VOIDED).exists():
        raise PaymentError("This payment has refunds. Void the refunds first.")
    payment.status = Payment.Status.VOIDED
    payment.voided_by = actor
    payment.voided_at = timezone.now()
    payment.void_reason = reason[:255]
    payment.save(update_fields=["status", "voided_by", "voided_at", "void_reason"])
    log_activity(Action.PAYMENT_VOIDED, f"{payment.reference} voided: {reason[:120]}", actor=actor, request=request)
    return payment


def refund_payment(payment, amount, method, paid_on, actor, reason, *, token=None, request=None):
    existing = _existing_for_token(token)
    if existing:
        return existing, False
    try:
        refund = _refund(payment, amount, method, paid_on, actor, reason, token)
    except IntegrityError:
        existing = _existing_for_token(token)
        if existing:
            return existing, False
        raise
    log_activity(Action.REFUND_RECORDED,
                 f"{refund.reference}: refunded LKR {amount:,.2f} of {payment.reference} ({reason.strip()[:80]})",
                 actor=actor, request=request)
    return refund, True


@transaction.atomic
def _refund(payment, amount, method, paid_on, actor, reason, token):
    _lock_target(payment.target)
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    reason = (reason or "").strip()
    if not reason:
        raise PaymentError("A reason is required for a refund.")
    _check_amount(amount)
    _check_date(paid_on)
    _check_reference(reason)
    if method not in PaymentMethod.values:
        raise PaymentError("Choose a valid refund method.")
    if payment.kind != Payment.Kind.PAYMENT or payment.status != Payment.Status.COMPLETED:
        raise PaymentError("Only completed payments can be refunded.")
    available = refundable_amount(payment)
    if amount > available:
        raise PaymentError(f"At most LKR {available:,.2f} of this payment can still be refunded.")
    return Payment.objects.create(
        member=payment.member, membership=payment.membership, appointment=payment.appointment,
        kind=Payment.Kind.REFUND,
        status=Payment.Status.COMPLETED, refund_of=payment, amount=amount, method=method, paid_on=paid_on,
        notes=reason[:255], recorded_by=actor, submission_token=token,
    )


@transaction.atomic
def adjust_balance(membership, kind, amount, reason, actor, request=None):
    membership = _lock_membership(membership)
    reason = (reason or "").strip()
    if not reason:
        raise PaymentError("A reason is required for a balance adjustment.")
    _check_amount(amount)
    _check_reference(reason)
    if kind not in BalanceAdjustment.Kind.values:
        raise PaymentError("Choose a valid adjustment type.")
    if membership.state == Membership.State.CANCELLED:
        raise PaymentError("Charges stop when a membership is cancelled, so it can't be adjusted.")
    balance = balance_for(membership)
    if kind == BalanceAdjustment.Kind.DISCOUNT and amount > balance.payable:
        raise PaymentError(
            f"A discount can't exceed the unpaid balance of LKR {balance.payable:,.2f}. "
            "Use a refund to return money already paid."
        )
    adjustment = BalanceAdjustment.objects.create(
        membership=membership, kind=kind, amount=amount, reason=reason[:255], created_by=actor
    )
    sign = "-" if kind == BalanceAdjustment.Kind.DISCOUNT else "+"
    log_activity(Action.BALANCE_ADJUSTED,
                 f"{adjustment.get_kind_display()} {sign}LKR {amount:,.2f} on {membership.member.member_id} "
                 f"({reason[:80]})", actor=actor, request=request)
    return adjustment
