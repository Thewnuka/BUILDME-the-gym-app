from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.core.models import ActivityLog
from apps.core.utils import log_activity
from apps.members.models import Member

from . import periods, services
from .forms import AssignMembershipForm, PlanForm, ReasonForm, RenewMembershipForm
from .models import Membership, MembershipPlan, MembershipStatus

PLAN_BREADCRUMB = ("Membership Plans", "memberships:plan_list")
MEMBERSHIP_BREADCRUMB = ("Memberships", "memberships:list")
EXPIRING_WINDOWS = (7, 14, 30)


def crumbs(*items):
    return [(label, reverse(name)) for label, name in items]


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------
class PlanListView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.view_memberships"
    template_name = "memberships/plans/list.html"
    context_object_name = "plans"
    paginate_by = 20
    page_title = "Membership Plans"

    def get_queryset(self):
        today = timezone.localdate()
        qs = MembershipPlan.objects.annotate(
            current_count=Count(
                "memberships",
                filter=Q(memberships__start_date__lte=today, memberships__end_date__gte=today)
                & ~Q(memberships__state=Membership.State.CANCELLED),
            )
        )
        self.q = self.request.GET.get("q", "").strip()[:100]
        self.status = self.request.GET.get("status", "")
        if self.q:
            qs = qs.filter(Q(name__icontains=self.q) | Q(description__icontains=self.q))
        if self.status == "active":
            qs = qs.filter(is_active=True)
        elif self.status == "inactive":
            qs = qs.filter(is_active=False)
        # Aggregation drops Meta.ordering, so order explicitly for stable pagination.
        return qs.order_by("-is_active", "price", "name")

    def get_context_data(self, **kwargs):
        user = self.request.user
        return super().get_context_data(
            filters={"q": self.q, "status": self.status},
            is_filtered=bool(self.q or self.status),
            can_create=user.has_perms(["accounts.manage_plans", "accounts.change_plan_prices"]),
            **kwargs,
        )


class PlanDetailView(ModulePermissionMixin, PageMetaMixin, DetailView):
    permission_required = "accounts.view_memberships"
    model = MembershipPlan
    template_name = "memberships/plans/detail.html"
    context_object_name = "plan"

    def get_page_title(self):
        return self.object.name

    def get_context_data(self, **kwargs):
        plan = self.object
        return super().get_context_data(
            breadcrumbs=crumbs(PLAN_BREADCRUMB),
            current_count=Membership.objects.current().filter(plan=plan).count(),
            total_count=plan.memberships.count(),
            recent=plan.memberships.select_related("member")[:10],
            example_end=periods.period_end(timezone.localdate(), plan.duration_value, plan.duration_unit),
            can_edit=self.request.user.has_perm("accounts.manage_plans"),
            **kwargs,
        )


class PlanFormMixin:
    model = MembershipPlan
    form_class = PlanForm
    template_name = "memberships/plans/form.html"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(),
                "can_change_price": self.request.user.has_perm("accounts.change_plan_prices")}


class PlanCreateView(ModulePermissionMixin, PageMetaMixin, PlanFormMixin, CreateView):
    # Creating a plan sets its price, so both permissions are needed.
    permission_required = ("accounts.manage_plans", "accounts.change_plan_prices")
    page_title = "New Membership Plan"

    def get_context_data(self, **kwargs):
        return super().get_context_data(breadcrumbs=crumbs(PLAN_BREADCRUMB), **kwargs)

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        log_activity(ActivityLog.Action.PLAN_CREATED,
                     f"Created plan '{self.object.name}' ({self.object.duration_display}, LKR {self.object.price:,.2f})",
                     request=self.request)
        messages.success(self.request, f"Plan '{self.object.name}' created.")
        return response


class PlanUpdateView(ModulePermissionMixin, PageMetaMixin, PlanFormMixin, UpdateView):
    permission_required = "accounts.manage_plans"

    def get_page_title(self):
        return f"Edit {self.object.name}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["breadcrumbs"] = crumbs(PLAN_BREADCRUMB) + [(self.object.name, self.object.get_absolute_url())]
        return context

    def form_valid(self, form):
        old = MembershipPlan.objects.get(pk=self.object.pk)
        response = super().form_valid(form)
        plan = self.object
        changes = []
        if old.price != plan.price:
            changes.append(f"price LKR {old.price:,.2f} → {plan.price:,.2f}")
        if (old.duration_value, old.duration_unit) != (plan.duration_value, plan.duration_unit):
            changes.append(f"duration {old.duration_display} → {plan.duration_display}")
        if old.is_active != plan.is_active:
            changes.append("activated" if plan.is_active else "deactivated")
        detail = f": {', '.join(changes)}" if changes else ""
        log_activity(ActivityLog.Action.PLAN_UPDATED, f"Updated plan '{plan.name}'{detail}", request=self.request)
        messages.success(self.request, f"Plan '{plan.name}' saved. Existing memberships keep their agreed terms.")
        return response


class PlanToggleActiveView(ModulePermissionMixin, View):
    permission_required = "accounts.manage_plans"
    http_method_names = ["post"]

    def post(self, request, pk):
        plan = get_object_or_404(MembershipPlan, pk=pk)
        plan.is_active = not plan.is_active
        plan.save(update_fields=["is_active", "updated_at"])
        state = "activated" if plan.is_active else "deactivated"
        log_activity(ActivityLog.Action.PLAN_UPDATED, f"Plan '{plan.name}' {state}", request=request)
        messages.success(request, f"Plan '{plan.name}' {state}.")
        return redirect(plan.get_absolute_url())


# --------------------------------------------------------------------------
# Membership lists
# --------------------------------------------------------------------------
class MembershipListView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.view_memberships"
    template_name = "memberships/list.html"
    context_object_name = "memberships"
    paginate_by = 20
    page_title = "Memberships"

    def get_queryset(self):
        g = self.request.GET
        self.filters = {
            "q": g.get("q", "").strip()[:100],
            "status": g.get("status", "") if g.get("status") in MembershipStatus.values else "",
            "plan": g.get("plan", "") if g.get("plan", "").isdigit() else "",
        }
        qs = Membership.objects.select_related("member", "plan")
        if self.filters["q"]:
            q = self.filters["q"]
            qs = qs.filter(Q(member__full_name__icontains=q) | Q(member__member_id__iexact=q) | Q(plan_name__icontains=q))
        if self.filters["status"]:
            qs = qs.with_status(self.filters["status"])
        if self.filters["plan"]:
            qs = qs.filter(plan_id=self.filters["plan"])
        return qs.order_by("-start_date", "-pk")

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            filters=self.filters, is_filtered=any(self.filters.values()),
            statuses=MembershipStatus.choices, plans=MembershipPlan.objects.order_by("name"),
            **kwargs,
        )


class ExpiringSoonView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.view_memberships"
    template_name = "memberships/expiring.html"
    context_object_name = "memberships"
    paginate_by = 25
    page_title = "Expiring Soon"

    def get_days(self):
        try:
            days = int(self.request.GET.get("days", 7))
        except ValueError:
            days = 7
        return days if days in EXPIRING_WINDOWS else 7

    def get_queryset(self):
        self.days = self.get_days()
        return Membership.objects.expiring_soon(self.days).select_related("member").order_by("end_date", "pk")

    def get_context_data(self, **kwargs):
        return super().get_context_data(days=self.days, windows=EXPIRING_WINDOWS,
                                        breadcrumbs=crumbs(MEMBERSHIP_BREADCRUMB), **kwargs)


class ExpiredView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.view_memberships"
    template_name = "memberships/expired.html"
    context_object_name = "memberships"
    paginate_by = 25
    page_title = "Expired Memberships"

    def get_queryset(self):
        self.include_inactive = self.request.GET.get("include_inactive") == "1"
        self.q = self.request.GET.get("q", "").strip()[:100]
        qs = Membership.objects.lapsed().select_related("member")
        if not self.include_inactive:
            qs = qs.filter(member__status=Member.Status.ACTIVE)
        if self.q:
            qs = qs.filter(Q(member__full_name__icontains=self.q) | Q(member__member_id__iexact=self.q))
        return qs.order_by("-end_date", "pk")

    def get_context_data(self, **kwargs):
        return super().get_context_data(include_inactive=self.include_inactive, filters={"q": self.q},
                                        breadcrumbs=crumbs(MEMBERSHIP_BREADCRUMB), **kwargs)


class MembershipDetailView(ModulePermissionMixin, PageMetaMixin, DetailView):
    permission_required = "accounts.view_memberships"
    model = Membership
    template_name = "memberships/detail.html"
    context_object_name = "membership"

    def get_queryset(self):
        return Membership.objects.select_related("member", "plan", "previous", "created_by")

    def get_page_title(self):
        return f"{self.object.plan_name} · {self.object.member.full_name}"

    def get_context_data(self, **kwargs):
        m = self.object
        latest = services.latest_period(m.member)
        return super().get_context_data(
            breadcrumbs=[("Members", reverse("members:list")), (m.member.full_name, m.member.get_absolute_url())],
            events=m.events.select_related("performed_by"),
            other_periods=m.member.memberships.exclude(pk=m.pk).order_by("-start_date")[:10],
            can_manage=self.request.user.has_perm("accounts.manage_memberships"),
            can_renew=latest is not None and latest.start_date <= timezone.localdate() and m.member.is_active,
            reason_form=ReasonForm(),
            **kwargs,
        )


# --------------------------------------------------------------------------
# Lifecycle actions
# --------------------------------------------------------------------------
def plan_options(start):
    """Active plans with the end date each would produce from ``start``."""
    return [
        {"plan": p, "end": periods.period_end(start, p.duration_value, p.duration_unit)}
        for p in MembershipPlan.objects.active().order_by("price", "name")
    ]


class AssignMembershipView(ModulePermissionMixin, PageMetaMixin, FormView):
    permission_required = "accounts.manage_memberships"
    form_class = AssignMembershipForm
    template_name = "memberships/assign.html"

    def dispatch(self, request, *args, **kwargs):
        self.member = get_object_or_404(Member, pk=kwargs["member_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_page_title(self):
        return f"Assign Membership · {self.member.full_name}"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "default_start": services.next_renewal_start(self.member)}

    def get_context_data(self, **kwargs):
        start = services.next_renewal_start(self.member)
        return super().get_context_data(
            member=self.member,
            breadcrumbs=[("Members", reverse("members:list")), (self.member.full_name, self.member.get_absolute_url())],
            latest=services.latest_period(self.member),
            options=plan_options(start), preview_start=start,
            **kwargs,
        )

    def form_valid(self, form):
        try:
            membership = services.assign_membership(
                self.member, form.cleaned_data["plan"], form.cleaned_data["start_date"],
                self.request.user, form.cleaned_data["notes"], self.request,
            )
        except services.MembershipError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(
            self.request,
            f"{membership.plan_name} assigned: {membership.start_date:%d %b %Y} – {membership.end_date:%d %b %Y}. "
            "Record the payment from the membership page.",
        )
        return redirect(membership.get_absolute_url())


class RenewMembershipView(ModulePermissionMixin, PageMetaMixin, FormView):
    permission_required = "accounts.manage_memberships"
    form_class = RenewMembershipForm
    template_name = "memberships/renew.html"

    def dispatch(self, request, *args, **kwargs):
        self.membership = get_object_or_404(Membership.objects.select_related("member"), pk=kwargs["pk"])
        self.member = self.membership.member
        return super().dispatch(request, *args, **kwargs)

    def get_page_title(self):
        return f"Renew Membership · {self.member.full_name}"

    def get_initial(self):
        latest = services.latest_period(self.member)
        plan = latest.plan if latest else self.membership.plan
        return {"plan": plan.pk} if plan.is_active else {}

    def get_context_data(self, **kwargs):
        latest = services.latest_period(self.member)
        start = services.next_renewal_start(self.member)
        return super().get_context_data(
            member=self.member, latest=latest, renewal_start=start,
            early=bool(latest and latest.end_date >= timezone.localdate()),
            blocked=bool(latest and latest.start_date > timezone.localdate()),
            options=plan_options(start),
            breadcrumbs=[("Members", reverse("members:list")), (self.member.full_name, self.member.get_absolute_url())],
            **kwargs,
        )

    def form_valid(self, form):
        try:
            membership = services.renew_membership(
                self.member, form.cleaned_data["plan"], self.request.user, form.cleaned_data["notes"], self.request
            )
        except services.MembershipError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        verb = "Plan change scheduled" if membership.kind == Membership.Kind.PLAN_CHANGE else "Membership renewed"
        messages.success(
            self.request,
            f"{verb}: {membership.plan_name}, {membership.start_date:%d %b %Y} – {membership.end_date:%d %b %Y}. "
            "Record the payment from the membership page.",
        )
        return redirect(membership.get_absolute_url())


class MembershipActionView(ModulePermissionMixin, View):
    """POST-only lifecycle transitions. Rules are enforced in services."""

    permission_required = "accounts.manage_memberships"
    http_method_names = ["post"]
    needs_reason = False

    def post(self, request, pk):
        membership = get_object_or_404(Membership.objects.select_related("member"), pk=pk)
        reason = ""
        if self.needs_reason:
            form = ReasonForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Please give a reason (at least 3 characters).")
                return redirect(membership.get_absolute_url())
            reason = form.cleaned_data["reason"]
        try:
            message = self.perform(membership, reason)
        except services.MembershipError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, message)
        return redirect(membership.get_absolute_url())


class SuspendMembershipView(MembershipActionView):
    needs_reason = True

    def perform(self, membership, reason):
        m = services.suspend_membership(membership, self.request.user, reason, self.request)
        return f"Membership suspended. The end date ({m.end_date:%d %b %Y}) is unchanged."


class ReactivateMembershipView(MembershipActionView):
    def perform(self, membership, reason):
        services.reactivate_membership(membership, self.request.user, self.request)
        return "Membership reactivated."


class CancelMembershipView(MembershipActionView):
    needs_reason = True

    def perform(self, membership, reason):
        services.cancel_membership(membership, self.request.user, reason, self.request)
        return "Membership cancelled. Any refund must be handled through payments."
