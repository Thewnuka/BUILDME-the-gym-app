"""Financial dashboard figures, computed with the same rules as the reports (finance.py)."""
from django.utils import timezone

from apps.dashboard.registry import register

from . import finance


@register("expenses_month")
def expenses_month(user):
    today = timezone.localdate()
    e = finance.expenses(today.replace(day=1), today)
    note = f"Operating; plus LKR {e['capital']:,.2f} equipment" if e["capital"] else f"Since 1 {today:%b} (operating)"
    return {"value": e["operating"], "note": note}


@register("profit_month")
def profit_month(user):
    today = timezone.localdate()
    s = finance.summary(today.replace(day=1), today)
    return {"value": s["operating_profit"], "note": "Collected − operating expenses (cash basis)"}


@register("revenue_vs_expenses")
def revenue_vs_expenses(user, months=6):
    today = timezone.localdate()
    first = finance.months_between(today.replace(day=1), today)[0]
    index = first.year * 12 + first.month - 1 - (months - 1)
    from datetime import date

    start = date(index // 12, index % 12 + 1, 1)
    labels, collected, spent = [], [], []
    for m in finance.months_between(start, today):
        end = min(finance.month_end(m), today)
        labels.append(m.strftime("%b %Y"))
        collected.append(float(finance.collected(m, end)["net"]))
        spent.append(float(finance.expenses(m, end)["operating"]))
    return {"labels": labels, "datasets": [{"label": "Net collected", "data": collected},
                                           {"label": "Operating expenses", "data": spent}]}
