"""Dashboard statistics from member profiles."""
from datetime import date

from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.dashboard.registry import register

from .models import Member


def _shift_month(d, months):
    index = d.year * 12 + (d.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


@register("total_members")
def total_members(user):
    counts = Member.objects.aggregate(
        total=Count("pk"), inactive=Count("pk", filter=Q(status=Member.Status.INACTIVE))
    )
    return {"value": counts["total"], "note": f"{counts['inactive']} inactive"}


@register("new_members_month")
def new_members_month(user):
    """Members registered from the 1st of the current month up to today."""
    today = timezone.localdate()
    count = Member.objects.filter(joined_on__gte=today.replace(day=1), joined_on__lte=today).count()
    return {"value": count, "note": f"Since 1 {today:%b %Y}"}


@register("member_growth")
def member_growth(user, months=6):
    today = timezone.localdate()
    first = _shift_month(today.replace(day=1), -(months - 1))
    rows = (
        Member.objects.filter(joined_on__gte=first, joined_on__lte=today)
        .annotate(month=TruncMonth("joined_on")).order_by().values("month").annotate(total=Count("pk"))
    )
    counts = {(r["month"].year, r["month"].month): r["total"] for r in rows}
    labels, data = [], []
    for i in range(months):
        m = _shift_month(first, i)
        labels.append(m.strftime("%b %Y"))
        data.append(counts.get((m.year, m.month), 0))
    return {"labels": labels, "data": data}
