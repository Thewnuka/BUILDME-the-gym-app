from collections import OrderedDict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, F, Max, Min, Q, Sum
from django.utils import timezone

from . import finance
from apps.trainers.access import can_view_sessions

from .base import Column, Report

ZERO = Decimal("0.00")
REGISTRY = OrderedDict()


def register(cls):
    REGISTRY[cls.slug] = cls
    return cls


def _bounds(params):
    start = timezone.make_aware(datetime.combine(params.date_from, time.min))
    end = timezone.make_aware(datetime.combine(params.date_to + timedelta(days=1), time.min))
    return start, end


def _periods(params):
    """Every day or month in the range (so empty periods show as zero)."""
    if params.group == "month":
        return finance.months_between(params.date_from, params.date_to)
    days = (params.date_to - params.date_from).days + 1
    return [params.date_from + timedelta(days=i) for i in range(days)]


def _period_label(d, group):
    return d.strftime("%b %Y") if group == "month" else d.strftime("%d %b %Y")


# ---------------------------------------------------------------------------
# Members & memberships
# ---------------------------------------------------------------------------
@register
class MemberRegistrations(Report):
    slug, title, section = "member-registrations", "Member registrations", "Members"
    description = "Members registered in the period."
    permissions = ("accounts.view_members",)
    searchable = True
    columns = [Column("member_id", "Member ID"), Column("name", "Name"), Column("phone", "Phone"),
               Column("email", "Email"), Column("joined_on", "Registered", "date"), Column("status", "Status")]

    def rows(self, params):
        from apps.members.models import Member

        qs = Member.objects.filter(joined_on__gte=params.date_from, joined_on__lte=params.date_to)
        if params.q:
            qs = qs.search(params.q)
        return [{"member_id": m.member_id, "name": m.full_name, "phone": m.phone, "email": m.email,
                 "joined_on": m.joined_on, "status": m.get_status_display(), "_url": m.get_absolute_url()}
                for m in qs.order_by("-joined_on", "-pk")]

    def summary(self, params, rows):
        return [("New members", len(rows), "int"),
                ("Still active", sum(1 for r in rows if r["status"] == "Active"), "int")]

    def chart(self, params, rows):
        counts = {}
        for r in rows:
            key = r["joined_on"].replace(day=1)
            counts[key] = counts.get(key, 0) + 1
        months = finance.months_between(params.date_from, params.date_to)
        return {"type": "bar", "label": "New members", "labels": [m.strftime("%b %Y") for m in months],
                "data": [counts.get(m, 0) for m in months]}


@register
class MembershipStatus(Report):
    slug, title, section = "membership-status", "Active & expired memberships", "Members"
    description = "Current memberships, or expired ones that were not renewed (as at today)."
    permissions = ("accounts.view_memberships",)
    uses_dates = False
    searchable = True
    columns = [Column("member_id", "Member ID"), Column("member", "Member"), Column("plan", "Plan"),
               Column("start", "Start", "date"), Column("end", "End", "date"), Column("status", "Status"),
               Column("days", "Days left / since", "int")]

    def extra_params(self, data):
        return {"status": data.get("status") if data.get("status") in ("active", "expired", "suspended") else "active"}

    def filter_controls(self, params):
        return [("status", "Show", [("active", "Active"), ("suspended", "Suspended"), ("expired", "Expired (not renewed)")],
                 params.extra["status"])]

    def rows(self, params):
        from apps.memberships.models import Membership, MembershipStatus as S

        status = params.extra["status"]
        qs = Membership.objects.lapsed() if status == "expired" else Membership.objects.with_status(
            S.SUSPENDED if status == "suspended" else S.ACTIVE)
        qs = qs.select_related("member")
        if params.q:
            qs = qs.filter(Q(member__full_name__icontains=params.q) | Q(member__member_id__iexact=params.q))
        today = timezone.localdate()
        return [{"member_id": m.member.member_id, "member": m.member.full_name, "plan": m.plan_name,
                 "start": m.start_date, "end": m.end_date, "status": m.status_label,
                 "days": abs((m.end_date - today).days), "_url": m.get_absolute_url()}
                for m in qs.order_by("end_date")]

    def summary(self, params, rows):
        from apps.memberships.models import Membership, MembershipStatus as S

        return [("Active", Membership.objects.with_status(S.ACTIVE).count(), "int"),
                ("Suspended", Membership.objects.with_status(S.SUSPENDED).count(), "int"),
                ("Expired, not renewed", Membership.objects.lapsed().count(), "int")]


@register
class Renewals(Report):
    slug, title, section = "renewals", "Membership renewals", "Members"
    description = "Renewals and plan changes recorded in the period."
    permissions = ("accounts.view_memberships",)
    searchable = True
    columns = [Column("created", "Recorded", "date"), Column("member", "Member"), Column("type", "Type"),
               Column("from_plan", "Previous plan"), Column("to_plan", "New plan"),
               Column("start", "Starts", "date"), Column("price", "Agreed price", "money")]

    def rows(self, params):
        from apps.memberships.models import Membership

        start, end = _bounds(params)
        qs = (Membership.objects.filter(kind__in=[Membership.Kind.RENEWAL, Membership.Kind.PLAN_CHANGE],
                                        created_at__gte=start, created_at__lt=end)
              .select_related("member", "previous"))
        if params.q:
            qs = qs.filter(Q(member__full_name__icontains=params.q) | Q(member__member_id__iexact=params.q))
        return [{"created": timezone.localtime(m.created_at).date(), "member": m.member.full_name,
                 "type": m.get_kind_display(), "from_plan": m.previous.plan_name if m.previous else "",
                 "to_plan": m.plan_name, "start": m.start_date, "price": m.price, "_url": m.get_absolute_url()}
                for m in qs.order_by("-created_at")]

    def summary(self, params, rows):
        return [("Renewals", sum(1 for r in rows if r["type"] == "Renewal"), "int"),
                ("Plan changes", sum(1 for r in rows if r["type"] != "Renewal"), "int"),
                ("Value agreed", sum((r["price"] for r in rows), ZERO), "money")]


@register
class PlanPopularity(Report):
    slug, title, section = "plan-popularity", "Membership plan popularity", "Members"
    description = "New sign-ups, renewals and current members per plan, with revenue collected in the period."
    permissions = ("accounts.view_memberships",)
    columns = [Column("plan", "Plan"), Column("new", "New sign-ups", "int"), Column("renewals", "Renewals", "int"),
               Column("changes_in", "Plan changes in", "int"), Column("current", "Current members", "int"),
               Column("revenue", "Net collected", "money")]

    def rows(self, params):
        from apps.memberships.models import Membership, MembershipPlan
        from apps.payments.models import Payment

        start, end = _bounds(params)
        created = Membership.objects.filter(created_at__gte=start, created_at__lt=end)
        current = Membership.objects.current()
        pays = Payment.objects.filter(status=Payment.Status.COMPLETED, membership__isnull=False,
                                      paid_on__gte=params.date_from, paid_on__lte=params.date_to)
        rows = []
        for plan in MembershipPlan.objects.all():
            p_pays = pays.filter(membership__plan=plan)
            net = finance._sum(p_pays, kind=Payment.Kind.PAYMENT) - finance._sum(p_pays, kind=Payment.Kind.REFUND)
            rows.append({"plan": plan.name, "new": created.filter(plan=plan, kind="new").count(),
                         "renewals": created.filter(plan=plan, kind="renewal").count(),
                         "changes_in": created.filter(plan=plan, kind="plan_change").count(),
                         "current": current.filter(plan=plan).count(), "revenue": net, "_url": plan.get_absolute_url()})
        return sorted(rows, key=lambda r: (-r["current"], -r["new"], r["plan"]))

    def chart(self, params, rows):
        return {"type": "bar", "label": "Current members", "labels": [r["plan"] for r in rows],
                "data": [r["current"] for r in rows]}


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------
@register
class AttendanceReport(Report):
    slug, title, section = "attendance", "Daily & monthly attendance", "Attendance"
    description = "Check-ins per day or month."
    permissions = ("accounts.view_attendance",)
    groupable = True
    columns = [Column("period", "Period"), Column("visits", "Visits", "int"), Column("members", "Unique members", "int"),
               Column("avg", "Avg. stay (min)", "int")]

    def rows(self, params):
        from apps.attendance.models import Attendance

        start, end = _bounds(params)
        qs = Attendance.objects.filter(checked_in_at__gte=start, checked_in_at__lt=end)
        buckets = {}
        for v in qs.values("checked_in_at", "checked_out_at", "member_id"):
            day = timezone.localtime(v["checked_in_at"]).date()
            key = day.replace(day=1) if params.group == "month" else day
            b = buckets.setdefault(key, {"visits": 0, "members": set(), "mins": []})
            b["visits"] += 1
            b["members"].add(v["member_id"])
            if v["checked_out_at"]:
                b["mins"].append((v["checked_out_at"] - v["checked_in_at"]).total_seconds() / 60)
        rows = []
        for p in _periods(params):
            b = buckets.get(p, {"visits": 0, "members": set(), "mins": []})
            rows.append({"period": _period_label(p, params.group), "visits": b["visits"], "members": len(b["members"]),
                         "avg": int(sum(b["mins"]) / len(b["mins"])) if b["mins"] else None})
        return rows

    def summary(self, params, rows):
        from apps.attendance.models import Attendance

        start, end = _bounds(params)
        qs = Attendance.objects.filter(checked_in_at__gte=start, checked_in_at__lt=end)
        return [("Visits", qs.count(), "int"), ("Unique members", qs.values("member").distinct().count(), "int")]

    def chart(self, params, rows):
        return {"type": "bar", "label": "Visits", "labels": [r["period"] for r in rows], "data": [r["visits"] for r in rows]}


@register
class MemberAttendance(Report):
    slug, title, section = "member-attendance", "Member attendance history", "Attendance"
    description = "Visits per member in the period (search for one member)."
    permissions = ("accounts.view_attendance",)
    searchable = True
    columns = [Column("member_id", "Member ID"), Column("member", "Member"), Column("visits", "Visits", "int"),
               Column("first", "First visit", "date"), Column("last", "Last visit", "date")]

    def rows(self, params):
        from apps.attendance.models import Attendance
        from apps.members.models import Member

        start, end = _bounds(params)
        qs = Attendance.objects.filter(checked_in_at__gte=start, checked_in_at__lt=end)
        if params.q:
            qs = qs.filter(member__in=Member.objects.search(params.q))
        agg = qs.order_by().values("member_id", "member__member_id", "member__full_name").annotate(
            n=Count("pk"), first=Min("checked_in_at"), last=Max("checked_in_at"))
        rows = [{"member_id": a["member__member_id"], "member": a["member__full_name"], "visits": a["n"],
                 "first": timezone.localtime(a["first"]).date(), "last": timezone.localtime(a["last"]).date(),
                 "_url": f"/attendance/?member={a['member_id']}&date_from={params.date_from}&date_to={params.date_to}"}
                for a in agg]
        return sorted(rows, key=lambda r: (-r["visits"], r["member"]))


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------
@register
class PaymentsReport(Report):
    slug, title, section = "payments", "Daily & monthly payments", "Finance"
    description = "Completed payments and refunds by payment date (pending and voided excluded)."
    permissions = ("accounts.view_payments",)
    groupable = True
    columns = [Column("period", "Period"), Column("count", "Payments", "int"), Column("gross", "Received", "money"),
               Column("refunds", "Refunded", "money"), Column("net", "Net collected", "money"),
               Column("cash", "Cash (net)", "money"), Column("bank_transfer", "Bank (net)", "money"),
               Column("card", "Card (net)", "money")]

    def rows(self, params):
        data = finance.payments_by_period(params.date_from, params.date_to, params.group)
        empty = {"gross": ZERO, "refunds": ZERO, "net": ZERO, "count": 0, "cash": ZERO, "bank_transfer": ZERO,
                 "card": ZERO}
        return [{"period": _period_label(p, params.group), **data.get(p, empty)} for p in _periods(params)]

    def summary(self, params, rows):
        c = finance.collected(params.date_from, params.date_to)
        return [("Received", c["gross"], "money"), ("Refunded", c["refunds"], "money"),
                ("Net collected", c["net"], "money")]

    def chart(self, params, rows):
        return {"type": "bar", "label": "Net collected (LKR)", "labels": [r["period"] for r in rows],
                "data": [float(r["net"]) for r in rows]}


@register
class OutstandingReport(Report):
    slug, title, section = "outstanding", "Outstanding balances", "Finance"
    description = "Amounts still owed today on memberships and training sessions (not revenue)."
    permissions = ("accounts.view_payments",)
    uses_dates = False
    searchable = True
    columns = [Column("member_id", "Member ID"), Column("member", "Member"), Column("item", "For"),
               Column("when", "Start", "date"), Column("due", "Amount due", "money"), Column("paid", "Paid", "money"),
               Column("outstanding", "Outstanding", "money")]

    def rows(self, params):
        from apps.appointments.models import Appointment
        from apps.memberships.models import Membership
        from apps.payments.balances import with_appointment_balances, with_balances

        ms = with_balances(Membership.objects.select_related("member")).filter(outstanding__gt=0)
        ss = with_appointment_balances(Appointment.objects.select_related("member", "session_type")).filter(outstanding__gt=0)
        if params.q:
            cond = Q(member__full_name__icontains=params.q) | Q(member__member_id__iexact=params.q)
            ms, ss = ms.filter(cond), ss.filter(cond)
        session_links = can_view_sessions(self.user)
        rows = [{"member_id": m.member.member_id, "member": m.member.full_name, "item": f"{m.plan_name} membership",
                 "when": m.start_date, "due": m.b_charge, "paid": m.b_net_paid, "outstanding": m.outstanding,
                 "_url": m.get_absolute_url()} for m in ms]
        rows += [{"member_id": s.member.member_id, "member": s.member.full_name, "item": s.session_type.name,
                  "when": timezone.localtime(s.starts_at).date(), "due": s.fee, "paid": s.b_net_paid,
                  "outstanding": s.outstanding, "_url": s.get_absolute_url() if session_links else ""} for s in ss]
        return sorted(rows, key=lambda r: -r["outstanding"])

    def summary(self, params, rows):
        o = finance.outstanding_now()
        return [("Memberships", o["memberships"], "money"), ("Session fees", o["sessions"], "money"),
                ("Total outstanding", o["total"], "money")]


@register
class RevenueReport(Report):
    slug, title, section = "revenue", "Revenue collected", "Finance"
    description = "Net money collected in the period, by membership plan and session type."
    permissions = ("accounts.view_payments",)
    columns = [Column("source", "Source"), Column("item", "Plan / session type"), Column("count", "Payments", "int"),
               Column("gross", "Received", "money"), Column("refunds", "Refunded", "money"),
               Column("net", "Net collected", "money")]

    def rows(self, params):
        from apps.payments.models import Payment

        qs = (Payment.objects.filter(status=Payment.Status.COMPLETED, paid_on__gte=params.date_from,
                                     paid_on__lte=params.date_to)
              .values("membership__plan_name", "appointment__session_type__name", "kind")
              .annotate(total=Sum("amount"), n=Count("pk")))
        buckets = {}
        for r in qs:
            if r["membership__plan_name"]:
                key = ("Membership", r["membership__plan_name"])
            else:
                key = ("Training session", r["appointment__session_type__name"] or "Session")
            b = buckets.setdefault(key, {"count": 0, "gross": ZERO, "refunds": ZERO})
            if r["kind"] == Payment.Kind.REFUND:
                b["refunds"] += r["total"]
            else:
                b["gross"] += r["total"]
                b["count"] += r["n"]
        return [{"source": k[0], "item": k[1], **v, "net": v["gross"] - v["refunds"]}
                for k, v in sorted(buckets.items(), key=lambda kv: -(kv[1]["gross"] - kv[1]["refunds"]))]

    def summary(self, params, rows):
        c = finance.collected(params.date_from, params.date_to)
        inv = finance.invoiced(params.date_from, params.date_to)
        return [("Invoiced (charges raised)", inv["total"], "money"), ("Received", c["gross"], "money"),
                ("Refunded", c["refunds"], "money"), ("Net collected", c["net"], "money")]

    def notes(self):
        return ("Invoiced = charges for memberships starting and sessions held in the period. Collected = money "
                "actually received by payment date. The two differ when people pay in advance or late.")


@register
class ExpensesReport(Report):
    slug, title, section = "expenses", "Expense report", "Finance"
    description = "Active expenses by category in the period (voided expenses excluded)."
    permissions = ("accounts.view_expenses",)
    columns = [Column("category", "Category"), Column("kind", "Type"), Column("count", "Entries", "int"),
               Column("total", "Total", "money"), Column("share", "Share", "pct")]

    def rows(self, params):
        from apps.expenses.models import CAPITAL_CATEGORIES, Expense, ExpenseCategory

        agg = (Expense.objects.filter(status=Expense.Status.ACTIVE, expense_date__gte=params.date_from,
                                      expense_date__lte=params.date_to)
               .values("category").annotate(total=Sum("amount"), n=Count("pk")))
        grand = sum((a["total"] for a in agg), ZERO) or Decimal("1")
        return sorted([{"category": ExpenseCategory(a["category"]).label,
                        "kind": "Capital" if a["category"] in CAPITAL_CATEGORIES else "Operating",
                        "count": a["n"], "total": a["total"], "share": float(a["total"] / grand * 100)}
                       for a in agg], key=lambda r: -r["total"])

    def summary(self, params, rows):
        e = finance.expenses(params.date_from, params.date_to)
        return [("Operating expenses", e["operating"], "money"), ("Capital (equipment)", e["capital"], "money"),
                ("Total spent", e["total"], "money")]

    def chart(self, params, rows):
        return {"type": "bar", "label": "LKR", "labels": [r["category"] for r in rows],
                "data": [float(r["total"]) for r in rows]}


@register
class RevenueVsExpenses(Report):
    slug, title, section = "revenue-vs-expenses", "Revenue vs expenses", "Finance"
    description = "Month by month: invoiced, collected, expenses and estimated operating profit."
    permissions = ("accounts.view_payments", "accounts.view_expenses")
    default_days = 180
    columns = [Column("month", "Month"), Column("invoiced", "Invoiced", "money"),
               Column("collected", "Net collected", "money"), Column("operating", "Operating expenses", "money"),
               Column("profit", "Est. operating profit", "money"), Column("capital", "Capital purchases", "money"),
               Column("after_capital", "After capital", "money")]

    def rows(self, params):
        rows = []
        for m in finance.months_between(params.date_from, params.date_to):
            start, end = max(m, params.date_from), min(finance.month_end(m), params.date_to)
            s = finance.summary(start, end)
            rows.append({"month": m.strftime("%b %Y"), "invoiced": finance.invoiced(start, end)["total"],
                         "collected": s["collected"]["net"], "operating": s["expenses"]["operating"],
                         "profit": s["operating_profit"], "capital": s["expenses"]["capital"],
                         "after_capital": s["after_capital"]})
        return rows

    def summary(self, params, rows):
        s = finance.summary(params.date_from, params.date_to)
        return [("Net collected", s["collected"]["net"], "money"), ("Operating expenses", s["expenses"]["operating"], "money"),
                ("Est. operating profit", s["operating_profit"], "money"),
                ("After capital purchases", s["after_capital"], "money")]

    def chart(self, params, rows):
        return {"type": "multi", "labels": [r["month"] for r in rows], "datasets": [
            {"label": "Net collected", "data": [float(r["collected"]) for r in rows]},
            {"label": "Operating expenses", "data": [float(r["operating"]) for r in rows]},
            {"label": "Est. profit", "data": [float(r["profit"]) for r in rows]}]}

    def notes(self):
        return ("Cash-basis estimate: profit = net revenue collected − operating expenses. Equipment purchases are "
                "shown separately as capital. Trainer pay records are not counted; payroll counts when recorded as a "
                "Salaries expense. Not an accounting statement.")


# ---------------------------------------------------------------------------
# Training, equipment, inventory
# ---------------------------------------------------------------------------
@register
class AppointmentsReport(Report):
    slug, title, section = "appointments", "Training appointments", "Training"
    description = "Sessions in the period by trainer and status."
    any_permissions = ("accounts.manage_appointments", "accounts.view_training")
    searchable = True
    columns = [Column("date", "Date", "date"), Column("time", "Time"), Column("trainer", "Trainer"),
               Column("member", "Member"), Column("type", "Session"), Column("status", "Status"),
               Column("fee", "Fee", "money")]

    def rows(self, params):
        from apps.appointments.models import Appointment
        from apps.trainers.access import trainer_profile_for

        start, end = _bounds(params)
        qs = Appointment.objects.filter(starts_at__gte=start, starts_at__lt=end).select_related(
            "trainer__user", "member", "session_type")
        profile = trainer_profile_for(self.user)
        if profile and not self.user.has_perm("accounts.manage_appointments"):
            qs = qs.filter(trainer=profile)
        if params.q:
            qs = qs.filter(Q(member__full_name__icontains=params.q) | Q(trainer__user__first_name__icontains=params.q)
                           | Q(trainer__user__last_name__icontains=params.q))
        return [{"date": timezone.localtime(a.starts_at).date(), "time": f"{timezone.localtime(a.starts_at):%H:%M}",
                 "trainer": a.trainer.name, "member": a.member.full_name, "type": a.session_type.name,
                 "status": a.get_status_display(), "fee": a.fee, "_url": a.get_absolute_url()}
                for a in qs.order_by("starts_at")]

    def summary(self, params, rows):
        out = [("Sessions", len(rows), "int")]
        for status in ("Completed", "No-show", "Cancelled", "Booked"):
            out.append((status, sum(1 for r in rows if r["status"] == status), "int"))
        return out


@register
class MaintenanceReport(Report):
    slug, title, section = "equipment-maintenance", "Equipment maintenance", "Operations"
    description = "Maintenance and repairs in the period, with open faults and upcoming services."
    any_permissions = ("accounts.manage_equipment", "accounts.report_equipment_faults")
    searchable = True
    columns = [Column("date", "Date", "date"), Column("code", "Code"), Column("equipment", "Equipment"),
               Column("kind", "Work"), Column("description", "Description"), Column("by", "Done by"),
               Column("cost", "Cost", "money")]

    def rows(self, params):
        from apps.equipment.models import MaintenanceRecord

        qs = MaintenanceRecord.objects.filter(performed_on__gte=params.date_from, performed_on__lte=params.date_to) \
            .select_related("equipment")
        if params.q:
            qs = qs.filter(Q(equipment__name__icontains=params.q) | Q(equipment__code__iexact=params.q))
        return [{"date": r.performed_on, "code": r.equipment.code, "equipment": r.equipment.name,
                 "kind": r.get_kind_display(), "description": r.description, "by": r.performed_by,
                 "cost": r.cost, "_url": r.equipment.get_absolute_url()} for r in qs]

    def summary(self, params, rows):
        from apps.equipment.models import Equipment, FaultReport
        from apps.equipment.services import maintenance_due

        return [("Jobs", len(rows), "int"), ("Maintenance cost", sum((r["cost"] or ZERO for r in rows), ZERO), "money"),
                ("Open faults", FaultReport.objects.filter(status="open").count(), "int"),
                ("Due within reminder window", maintenance_due().count(), "int"),
                ("Out of service now", Equipment.objects.filter(status="out_of_service").count(), "int")]


@register
class InventoryReport(Report):
    slug, title, section = "inventory", "Inventory & low stock", "Operations"
    description = "Stock on hand, movements in the period and items at or below minimum."
    any_permissions = ("accounts.manage_inventory", "accounts.use_inventory")
    searchable = True
    columns = [Column("sku", "SKU"), Column("name", "Item"), Column("on_hand", "On hand", "decimal"),
               Column("minimum", "Minimum", "decimal"), Column("low", "Low?"), Column("in", "Received", "decimal"),
               Column("out", "Used / sold", "decimal"), Column("value", "Stock value", "money")]

    def extra_params(self, data):
        return {"low": data.get("low") == "1"}

    def filter_controls(self, params):
        return [("low", "Items", [("", "All active items"), ("1", "Low stock only")], "1" if params.extra["low"] else "")]

    def rows(self, params):
        from apps.inventory.models import InventoryItem

        start, end = _bounds(params)
        qs = InventoryItem.objects.filter(is_active=True).annotate(
            received=Sum("movements__quantity_change", filter=Q(movements__quantity_change__gt=0,
                                                                movements__created_at__gte=start,
                                                                movements__created_at__lt=end)),
            used=Sum("movements__quantity_change", filter=Q(movements__quantity_change__lt=0,
                                                            movements__created_at__gte=start,
                                                            movements__created_at__lt=end)),
        )
        if params.extra["low"]:
            qs = qs.filter(quantity__lte=F("minimum_stock"))
        if params.q:
            qs = qs.filter(Q(name__icontains=params.q) | Q(sku__iexact=params.q))
        return [{"sku": i.sku, "name": i.name, "on_hand": i.quantity, "minimum": i.minimum_stock,
                 "low": "Yes" if i.is_low else "", "in": i.received or 0, "out": -(i.used or 0),
                 "value": (i.quantity * i.unit_cost) if i.unit_cost is not None else None,
                 "_url": i.get_absolute_url()} for i in qs.order_by("name")]

    def summary(self, params, rows):
        from apps.inventory.services import low_stock_items

        return [("Items", len(rows), "int"), ("Low stock", low_stock_items().count(), "int"),
                ("Stock value (known costs)", sum((r["value"] or ZERO for r in rows), ZERO), "money")]

