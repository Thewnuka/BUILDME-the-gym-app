"""
Financial calculation rules (used by every report and the dashboard).

    Invoiced (charges raised)
        Membership periods whose START DATE falls in the period: agreed price
        ± balance adjustments; a cancelled membership counts only what was
        paid (its charge stopped).
      + Training sessions whose START falls in the period and are not
        cancelled: the fee fixed at booking.

    Collected (gross)      completed payments, by payment date.
    Refunds                completed refunds, by refund date.
    Net revenue collected  = collected − refunds.
                           Pending (unconfirmed) and voided transactions are excluded.
    Outstanding            amounts still owed right now (as at today, not per
                           period): memberships + session fees.

    Operating expenses     active (non-voided) expenses by expense date,
                           EXCLUDING capital purchases (equipment).
    Capital purchases      active expenses in the Equipment category.
    Estimated operating profit = net revenue collected − operating expenses.
    Cash surplus after capital = operating profit − capital purchases.

No double counting:
* each payment, refund and expense is one database row, counted once;
* repair costs and stock/equipment purchases exist only as their linked
  expense (the source records just point to it);
* trainer pay records are private HR notes and are NOT counted. Payroll is
  counted when the accountant records it as a "Salaries" expense.
This is a cash-basis estimate, not an accounting statement (no accruals,
depreciation or tax).
"""
from datetime import date
from decimal import Decimal

from django.db.models import Count, DecimalField, Sum
from django.db.models.functions import Coalesce, TruncDay, TruncMonth

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=14, decimal_places=2)


def _sum(qs, field="amount", **filters):
    return qs.filter(**filters).aggregate(t=Coalesce(Sum(field), ZERO, output_field=MONEY))["t"]


def collected(start, end):
    from apps.payments.models import Payment

    done = Payment.objects.filter(status=Payment.Status.COMPLETED, paid_on__gte=start, paid_on__lte=end)
    gross = _sum(done, kind=Payment.Kind.PAYMENT)
    refunds = _sum(done, kind=Payment.Kind.REFUND)
    return {"gross": gross, "refunds": refunds, "net": gross - refunds,
            "count": done.filter(kind=Payment.Kind.PAYMENT).count()}


def invoiced(start, end):
    from apps.appointments.models import Appointment
    from apps.memberships.models import Membership
    from apps.payments.balances import balance_for

    memberships = Membership.objects.filter(start_date__gte=start, start_date__lte=end)
    membership_total = sum((balance_for(m).charge for m in memberships), ZERO)
    sessions = Appointment.objects.exclude(status=Appointment.Status.CANCELLED).filter(
        starts_at__date__gte=start, starts_at__date__lte=end)
    session_total = _sum(sessions, "fee")
    return {"memberships": membership_total, "sessions": session_total, "total": membership_total + session_total}


def expenses(start, end):
    from apps.expenses.models import CAPITAL_CATEGORIES, Expense

    active = Expense.objects.filter(status=Expense.Status.ACTIVE, expense_date__gte=start, expense_date__lte=end)
    capital = _sum(active.filter(category__in=CAPITAL_CATEGORIES))
    operating = _sum(active.exclude(category__in=CAPITAL_CATEGORIES))
    return {"operating": operating, "capital": capital, "total": operating + capital}


def outstanding_now():
    from apps.appointments.models import Appointment
    from apps.payments.balances import with_appointment_balances, with_balances

    memberships = with_balances().filter(outstanding__gt=0).aggregate(
        t=Coalesce(Sum("outstanding"), ZERO, output_field=MONEY), n=Count("pk"))
    sessions = with_appointment_balances(Appointment.objects.all()).filter(outstanding__gt=0).aggregate(
        t=Coalesce(Sum("outstanding"), ZERO, output_field=MONEY), n=Count("pk"))
    return {"memberships": memberships["t"], "sessions": sessions["t"], "total": memberships["t"] + sessions["t"],
            "count": memberships["n"] + sessions["n"]}


def summary(start, end):
    cash, exp = collected(start, end), expenses(start, end)
    profit = cash["net"] - exp["operating"]
    return {"collected": cash, "expenses": exp, "operating_profit": profit, "after_capital": profit - exp["capital"]}


def months_between(start, end):
    months, current = [], date(start.year, start.month, 1)
    while current <= end:
        months.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return months


def month_end(month_start):
    nxt = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
    from datetime import timedelta

    return nxt - timedelta(days=1)


def payments_by_period(start, end, group="day"):
    """{period_date: {gross, refunds, net, count, cash, bank_transfer, card}} for completed transactions."""
    from apps.payments.models import Payment

    trunc = TruncMonth("paid_on") if group == "month" else TruncDay("paid_on")
    rows = (Payment.objects.filter(status=Payment.Status.COMPLETED, paid_on__gte=start, paid_on__lte=end)
            .annotate(period=trunc).order_by().values("period", "kind", "method")
            .annotate(total=Sum("amount"), n=Count("pk")))
    out = {}
    for r in rows:
        key = r["period"].date() if hasattr(r["period"], "date") else r["period"]
        bucket = out.setdefault(key, {"gross": ZERO, "refunds": ZERO, "count": 0, "cash": ZERO,
                                      "bank_transfer": ZERO, "card": ZERO})
        if r["kind"] == Payment.Kind.REFUND:
            bucket["refunds"] += r["total"]
            bucket[r["method"]] -= r["total"]
        else:
            bucket["gross"] += r["total"]
            bucket["count"] += r["n"]
            bucket[r["method"]] += r["total"]
    for bucket in out.values():
        bucket["net"] = bucket["gross"] - bucket["refunds"]
    return out


