from django import template

from apps.memberships.models import Membership
from apps.payments.balances import balance_for, balance_from_annotations, with_appointment_balances, with_balances
from apps.payments.models import Payment

register = template.Library()


def _can_view(user, member):
    return user.has_perm("accounts.view_payments") or (member.user_id is not None and member.user_id == user.pk)


@register.filter
def membership_balance(membership):
    """Balance object for one membership (use in small lists only)."""
    return balance_for(membership)


@register.inclusion_tag("payments/_membership_panel.html", takes_context=True)
def membership_payments(context, membership):
    user = context["request"].user
    balance = balance_for(membership)
    return {
        "membership": membership,
        "balance": balance,
        "visible": _can_view(user, membership.member),
        "payments": membership.payments.select_related("recorded_by").order_by("-recorded_at")[:20],
        "adjustments": membership.adjustments.select_related("created_by"),
        "can_record": user.has_perm("accounts.manage_payments") and balance.payable > 0,
        "can_adjust": user.has_perm("accounts.refund_payments") and membership.state != Membership.State.CANCELLED,
        "is_staff_view": user.has_perm("accounts.view_payments"),
    }


@register.inclusion_tag("payments/_member_panel.html", takes_context=True)
def member_payments(context, member, limit=8):
    user = context["request"].user
    visible = _can_view(user, member)
    memberships = with_balances(member.memberships.all()) if visible else []
    sessions = list(with_appointment_balances(member.appointments.select_related("session_type", "trainer__user"))
                    .filter(outstanding__gt=0)) if visible else []
    outstanding = sum((m.outstanding for m in memberships), 0) + sum((s.outstanding for s in sessions), 0)
    return {
        "member": member,
        "visible": visible,
        "payments": member.payments.exclude(status=Payment.Status.VOIDED).order_by("-paid_on", "-pk")[:limit]
        if visible else [],
        "outstanding": outstanding,
        "unpaid": [m for m in memberships if m.outstanding > 0],
        "unpaid_sessions": sessions,
        "is_staff_view": user.has_perm("accounts.view_payments"),
        "can_record": user.has_perm("accounts.manage_payments"),
    }


@register.simple_tag
def balance_of(membership):
    if hasattr(membership, "b_paid"):
        return balance_from_annotations(membership)
    return balance_for(membership)
