from decimal import Decimal

from apps.members.models import Member
from apps.memberships import periods
from apps.memberships.models import Membership, MembershipPlan


def make_member(**extra):
    n = Member.objects.count() + 1
    extra.setdefault("full_name", f"Member {n}")
    extra.setdefault("phone", f"+9477{n:07d}")
    return Member.objects.create(**extra)


def make_plan(name=None, months=1, days=None, price="5000.00", **extra):
    n = MembershipPlan.objects.count() + 1
    unit, value = (periods.DAY, days) if days else (periods.MONTH, months)
    return MembershipPlan.objects.create(
        name=name or f"Plan {n}", duration_value=value, duration_unit=unit, price=Decimal(price), **extra
    )


def make_membership(member, plan, start, **extra):
    """Create a period directly (bypassing service date limits) for test setup."""
    return Membership.objects.create(
        member=member, plan=plan, plan_name=plan.name, duration_value=plan.duration_value,
        duration_unit=plan.duration_unit, price=plan.price, start_date=start,
        end_date=periods.period_end(start, plan.duration_value, plan.duration_unit), **extra,
    )
