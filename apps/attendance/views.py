from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import ListView, TemplateView

from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.members.models import Member
from apps.memberships.models import Membership

from . import services
from .models import Attendance
from .qr import qr_svg

MAX_RANGE_DAYS = 366


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def day_bounds(day):
    start = timezone.make_aware(datetime.combine(day, time.min))
    return start, start + timedelta(days=1)


def flash_result(request, result):
    if result.ok:
        messages.success(request, result.message)
        for warning in result.warnings:
            messages.warning(request, warning)
    else:
        messages.error(request, result.message)


class CheckInDeskView(ModulePermissionMixin, PageMetaMixin, TemplateView):
    """Front desk: search and check in manually, scan QR codes, check members out."""

    permission_required = "accounts.manage_attendance"
    template_name = "attendance/desk.html"
    page_title = "Check-in Desk"

    def get_context_data(self, **kwargs):
        services.close_stale_visits()
        q = self.request.GET.get("q", "").strip()[:100]
        results = []
        if q:
            current = Membership.objects.current()
            results = list(
                Member.objects.search(q).select_related("user").order_by("full_name")[:10]
            )
            open_ids = set(Attendance.objects.filter(member__in=results, checked_out_at__isnull=True,
                                                     auto_closed=False).values_list("member_id", flat=True))
            current_by_member = {m.member_id: m for m in current.filter(member__in=results)}
            for member in results:
                member.is_in = member.pk in open_ids
                member.current_membership = current_by_member.get(member.pk)
        start, end = day_bounds(timezone.localdate())
        today = Attendance.objects.filter(checked_in_at__gte=start, checked_in_at__lt=end)
        return super().get_context_data(
            q=q, results=results,
            in_gym=Attendance.objects.filter(checked_out_at__isnull=True, auto_closed=False)
            .select_related("member", "membership").order_by("-checked_in_at"),
            today_count=today.count(),
            recent=today.select_related("member").order_by("-checked_in_at")[:8],
            **kwargs,
        )


class _DeskActionView(ModulePermissionMixin, View):
    permission_required = "accounts.manage_attendance"
    http_method_names = ["post"]

    def back(self):
        target = self.request.POST.get("next") or reverse("attendance:desk")
        if not url_has_allowed_host_and_scheme(target, {self.request.get_host()}, self.request.is_secure()):
            target = reverse("attendance:desk")
        return redirect(target)


class ManualCheckInView(_DeskActionView):
    def post(self, request):
        member = get_object_or_404(Member, pk=request.POST.get("member"))
        flash_result(request, services.check_in(member, request.user, Attendance.Method.MANUAL, request))
        return self.back()


class QRCheckInView(_DeskActionView):
    def post(self, request):
        flash_result(request, services.check_in_by_qr(request.POST.get("token", ""), request.user, request))
        return self.back()


class CheckOutView(_DeskActionView):
    def post(self, request, pk):
        attendance = get_object_or_404(Attendance, pk=pk)
        flash_result(request, services.check_out(attendance, request.user))
        return self.back()


class AttendanceListView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.view_attendance"
    template_name = "attendance/list.html"
    context_object_name = "visits"
    paginate_by = 30
    page_title = "Attendance History"

    def get_queryset(self):
        services.close_stale_visits()
        g = self.request.GET
        today = timezone.localdate()
        date_to = _parse_date(g.get("date_to")) or today
        date_from = _parse_date(g.get("date_from")) or date_to - timedelta(days=6)
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        if (date_to - date_from).days > MAX_RANGE_DAYS:
            date_from = date_to - timedelta(days=MAX_RANGE_DAYS)
        self.member = None
        if g.get("member", "").isdigit():
            self.member = Member.objects.filter(pk=g["member"]).first()
        self.filters = f = {
            "q": g.get("q", "").strip()[:100], "date_from": date_from, "date_to": date_to,
            "status": g.get("status", "") if g.get("status") in ("open", "closed", "auto") else "",
            "method": g.get("method", "") if g.get("method") in Attendance.Method.values else "",
        }
        start, _ = day_bounds(date_from)
        _, end = day_bounds(date_to)
        qs = Attendance.objects.select_related("member", "membership", "checked_in_by").filter(
            checked_in_at__gte=start, checked_in_at__lt=end
        )
        if self.member:
            qs = qs.filter(member=self.member)
        if f["q"]:
            qs = qs.filter(member__in=Member.objects.search(f["q"]))
        if f["status"] == "open":
            qs = qs.filter(checked_out_at__isnull=True, auto_closed=False)
        elif f["status"] == "closed":
            qs = qs.filter(checked_out_at__isnull=False)
        elif f["status"] == "auto":
            qs = qs.filter(auto_closed=True)
        if f["method"]:
            qs = qs.filter(method=f["method"])
        self.summary = qs.aggregate(visits=Count("pk"), members=Count("member", distinct=True))
        return qs.order_by("-checked_in_at")

    def get_context_data(self, **kwargs):
        return super().get_context_data(filters=self.filters, member_filter=self.member, summary=self.summary,
                                        methods=Attendance.Method.choices, **kwargs)


class DailyReportView(ModulePermissionMixin, PageMetaMixin, TemplateView):
    permission_required = "accounts.view_attendance"
    template_name = "attendance/daily.html"
    page_title = "Daily Attendance Report"

    def get_context_data(self, **kwargs):
        services.close_stale_visits()
        day = _parse_date(self.request.GET.get("date")) or timezone.localdate()
        start, end = day_bounds(day)
        visits = list(
            Attendance.objects.filter(checked_in_at__gte=start, checked_in_at__lt=end)
            .select_related("member", "membership").order_by("checked_in_at")
        )
        hourly = {}
        for v in visits:
            hour = timezone.localtime(v.checked_in_at).hour
            hourly[hour] = hourly.get(hour, 0) + 1
        first_hour = min(list(hourly) + [6])
        last_hour = max(list(hourly) + [21])
        hours = list(range(first_hour, last_hour + 1))
        completed = [v.duration for v in visits if v.checked_out_at]
        avg_minutes = int(sum(d.total_seconds() for d in completed) / len(completed) // 60) if completed else None
        by_plan = {}
        for v in visits:
            by_plan[v.membership.plan_name] = by_plan.get(v.membership.plan_name, 0) + 1
        peak = max(hourly.items(), key=lambda kv: kv[1]) if hourly else None
        return super().get_context_data(
            day=day, prev_day=day - timedelta(days=1),
            next_day=day + timedelta(days=1) if day < timezone.localdate() else None,
            visits=visits,
            stats={
                "visits": len(visits), "members": len({v.member_id for v in visits}),
                "avg_minutes": avg_minutes, "peak": peak,
                "no_checkout": sum(1 for v in visits if v.auto_closed),
                "in_gym": sum(1 for v in visits if v.is_open),
                "with_balance": sum(1 for v in visits if v.balance_due_at_checkin),
            },
            chart={"labels": [f"{h:02d}:00" for h in hours], "data": [hourly.get(h, 0) for h in hours]},
            by_plan=sorted(by_plan.items(), key=lambda kv: -kv[1]),
            **kwargs,
        )


class MemberPassView(PageMetaMixin, TemplateView):
    """A member's printable QR pass: front-desk staff (it works as an entry credential), or the member themself."""

    template_name = "attendance/pass.html"

    def dispatch(self, request, *args, **kwargs):
        self.member = get_object_or_404(Member, pk=kwargs["member_pk"])
        is_self = self.member.user_id is not None and self.member.user_id == request.user.pk
        if not (request.user.has_perm("accounts.manage_attendance") or is_self):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_page_title(self):
        return f"Check-in pass · {self.member.full_name}"

    def get_context_data(self, **kwargs):
        card = services.get_pass(self.member)
        user = self.request.user
        crumbs = [("Members", reverse("members:list")), (self.member.full_name, self.member.get_absolute_url())] \
            if user.has_perm("accounts.view_members") else []
        return super().get_context_data(
            member=self.member, card=card, svg=qr_svg(card.qr_payload, scale=8), breadcrumbs=crumbs,
            can_reissue=user.has_perm("accounts.manage_attendance"), **kwargs,
        )


class ReissuePassView(ModulePermissionMixin, View):
    permission_required = "accounts.manage_attendance"
    http_method_names = ["post"]

    def post(self, request, member_pk):
        member = get_object_or_404(Member, pk=member_pk)
        services.reissue_pass(member, request.user, request)
        messages.success(request, "A new QR pass was issued. The previous code no longer works.")
        return redirect("attendance:pass", member_pk=member.pk)
