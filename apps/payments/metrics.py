"""
Financial dashboard figures.

"Collected" figures count only completed money (payments minus refunds) by
payment date. "Outstanding" is money still owed on non-cancelled memberships,
which is never mixed into revenue.
"""
from datetime import date

from django.db.models import Count, DecimalField, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from apps.dashboard.registry import register
from apps.memberships.models import Membership

from .balances import ZERO, PaymentStatus, balance_from_annotations, with_appointment_balances, with_balances
from .models import Payment

MONEY = DecimalField(max_digits=12, decimal_places=2)


def _net_collected(qs):
    totals = qs.filter(status=Payment.Status.COMPLETED).aggregate(
        received=Coalesce(Sum("amount", filter=Q(kind=Payment.Kind.PAYMENT)), ZERO, output_field=MONEY),
        refunded=Coalesce(Sum("amount", filter=Q(kind=Payment.Kind.REFUND)), ZERO, output_field=MONEY),
        count=Count("pk", filter=Q(kind=Payment.Kind.PAYMENT)),
    )
    return totals["received"] - totals["refunded"], totals["count"], totals["refunded"]


def _shift_month(d, months):
    index = d.year * 12 + (d.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


@register("today_revenue")
def today_revenue(user):
    net, count, refunded = _net_collected(Payment.objects.filter(paid_on=timezone.localdate()))
    note = f"{count} payment{'s' if count != 1 else ''} collected"
    if refunded:
        note += f" · LKR {refunded:,.2f} refunded"
    return {"value": net, "note": note}


@register("monthly_revenue")
def monthly_revenue(user):
    today = timezone.localdate()
    net, count, _ = _net_collected(Payment.objects.filter(paid_on__gte=today.replace(day=1), paid_on__lte=today))
    return {"value": net, "note": f"Collected since 1 {today:%b} (net of refunds)"}


@register("outstanding_payments")
def outstanding_payments(user):
    from apps.appointments.models import Appointment

    qs = with_balances().filter(outstanding__gt=0)
    totals = qs.aggregate(total=Coalesce(Sum("outstanding"), ZERO, output_field=MONEY), count=Count("pk"))
    sessions = with_appointment_balances(Appointment.objects.all()).filter(outstanding__gt=0)
    s_totals = sessions.aggregate(total=Coalesce(Sum("outstanding"), ZERO, output_field=MONEY), count=Count("pk"))
    note = f"Unpaid on {totals['count']} membership{'s' if totals['count'] != 1 else ''}"
    if s_totals["count"]:
        note += f" · {s_totals['count']} session fee{'s' if s_totals['count'] != 1 else ''}"
    return {"value": totals["total"] + s_totals["total"], "note": note}


@register("revenue_trend")
def revenue_trend(user, months=6):
    today = timezone.localdate()
    first = _shift_month(today.replace(day=1), -(months - 1))
    rows = (
        Payment.objects.filter(status=Payment.Status.COMPLETED, paid_on__gte=first, paid_on__lte=today)
        .annotate(month=TruncMonth("paid_on")).order_by().values("month", "kind").annotate(total=Sum("amount"))
    )
    net = {}
    for r in rows:
        key = (r["month"].year, r["month"].month)
        sign = -1 if r["kind"] == Payment.Kind.REFUND else 1
        net[key] = net.get(key, ZERO) + sign * r["total"]
    labels, data = [], []
    for i in range(months):
        m = _shift_month(first, i)
        labels.append(m.strftime("%b %Y"))
        data.append(float(net.get((m.year, m.month), ZERO)))
    return {"labels": labels, "data": data}


@register("recent_payments")
def recent_payments(user, limit=6):
    return list(Payment.objects.select_related("member").exclude(status=Payment.Status.VOIDED)[:limit])


@register("payment_breakdown")
def payment_breakdown(user):
    """Payment status of every current or scheduled (non-cancelled) membership."""
    counts = {key: 0 for key in PaymentStatus.LABELS}
    for m in with_balances(Membership.objects.open()):
        counts[balance_from_annotations(m).status] += 1
    rows = [
        {"key": k, "label": PaymentStatus.LABELS[k], "badge": PaymentStatus.BADGES[k], "count": c}
        for k, c in counts.items() if c or k in (PaymentStatus.PAID, PaymentStatus.PARTIALLY_PAID, PaymentStatus.UNPAID)
    ]
    return {"rows": rows, "total": sum(counts.values())}
