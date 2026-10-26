import mimetypes
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Exists, OuterRef, Subquery
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.core.models import ActivityLog
from apps.core.utils import log_activity
from apps.memberships.models import Membership, MembershipStatus
from apps.memberships.services import next_renewal_start

from .forms import MemberCreateForm, MemberDeactivateForm, MemberForm
from .models import Member
from .services import MemberStateError, create_portal_login, deactivate_member, reactivate_member, register_member

SORT_OPTIONS = {
    "-joined": ("-joined_on", "-pk"),
    "joined": ("joined_on", "pk"),
    "name": ("full_name", "pk"),
    "-name": ("-full_name", "-pk"),
    "id": ("member_id",),
    "-id": ("-member_id",),
}
SORT_LABELS = {
    "-joined": "Newest first", "joined": "Oldest first", "name": "Name A–Z",
    "-name": "Name Z–A", "id": "Member ID ↑", "-id": "Member ID ↓",
}
MEMBERSHIP_FILTERS = {
    "current": "Current membership",
    "expiring": "Expiring soon",
    "lapsed": "Expired (not renewed)",
    "none": "Never had a membership",
}


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def members_breadcrumb():
    return [("Members", reverse("members:list"))]


class MemberListView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.view_members"
    template_name = "members/list.html"
    context_object_name = "members"
    paginate_by = 20
    page_title = "Members"

    def get_filters(self):
        g = self.request.GET
        sort = g.get("sort", "-joined")
        return {
            "q": g.get("q", "").strip()[:100],
            "status": g.get("status", "") if g.get("status") in Member.Status.values else "",
            "membership": g.get("membership", "") if g.get("membership") in MEMBERSHIP_FILTERS else "",
            "joined_from": _parse_date(g.get("joined_from")),
            "joined_to": _parse_date(g.get("joined_to")),
            "sort": sort if sort in SORT_OPTIONS else "-joined",
        }

    def get_queryset(self):
        f = self.filters = self.get_filters()
        current = Membership.objects.current().filter(member=OuterRef("pk")).order_by("-end_date")
        qs = Member.objects.search(f["q"]).annotate(
            current_plan=Subquery(current.values("plan_name")[:1]),
            current_end=Subquery(current.values("end_date")[:1]),
            current_state=Subquery(current.values("state")[:1]),
        )
        if f["status"]:
            qs = qs.filter(status=f["status"])
        if f["joined_from"]:
            qs = qs.filter(joined_on__gte=f["joined_from"])
        if f["joined_to"]:
            qs = qs.filter(joined_on__lte=f["joined_to"])
        if f["membership"] == "current":
            qs = qs.filter(Exists(Membership.objects.current().filter(member=OuterRef("pk"))))
        elif f["membership"] == "expiring":
            qs = qs.filter(Exists(Membership.objects.expiring_soon().filter(member=OuterRef("pk"))))
        elif f["membership"] == "lapsed":
            qs = qs.filter(Exists(Membership.objects.lapsed().filter(member=OuterRef("pk"))))
        elif f["membership"] == "none":
            qs = qs.filter(~Exists(Membership.objects.filter(member=OuterRef("pk"))))
        return qs.order_by(*SORT_OPTIONS[f["sort"]])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        f = self.filters
        context.update(
            filters={**f, "joined_from": f["joined_from"] or "", "joined_to": f["joined_to"] or ""},
            is_filtered=any([f["q"], f["status"], f["membership"], f["joined_from"], f["joined_to"]]),
            statuses=Member.Status.choices,
            membership_filters=MEMBERSHIP_FILTERS.items(),
            sort_options=SORT_LABELS.items(),
            can_manage=self.request.user.has_perm("accounts.manage_members"),
        )
        return context


class MemberDetailView(ModulePermissionMixin, PageMetaMixin, DetailView):
    permission_required = "accounts.view_members"
    model = Member
    template_name = "members/detail.html"
    context_object_name = "member"
    breadcrumbs = None

    def get_page_title(self):
        return self.object.full_name

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        member = self.object
        user = self.request.user
        history = list(member.memberships.select_related("plan").order_by("-start_date", "-pk"))
        current = next((m for m in history if m.status in (MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED)), None)
        scheduled = [m for m in history if m.status == MembershipStatus.SCHEDULED]
        latest = next((m for m in history if m.status != MembershipStatus.CANCELLED), None)
        context.update(
            breadcrumbs=members_breadcrumb(),
            history=history,
            current=current,
            scheduled=scheduled,
            latest=latest,
            renewal_start=next_renewal_start(member) if latest else None,
            can_manage=user.has_perm("accounts.manage_members"),
            can_manage_memberships=user.has_perm("accounts.manage_memberships"),
            deactivate_form=MemberDeactivateForm(),
            activities=ActivityLog.objects.filter(description__contains=member.member_id)[:8]
            if user.has_perm("accounts.view_activity_log") else None,
        )
        return context


class MemberCreateView(ModulePermissionMixin, PageMetaMixin, CreateView):
    permission_required = "accounts.manage_members"
    model = Member
    form_class = MemberCreateForm
    template_name = "members/form.html"
    page_title = "Register Member"

    def get_context_data(self, **kwargs):
        return super().get_context_data(breadcrumbs=members_breadcrumb(), **kwargs)

    def form_valid(self, form):
        self.object = register_member(form, self.request.user, self.request)
        messages.success(self.request, f"Member {self.object.member_id} registered successfully.")
        if form.cleaned_data.get("create_login"):
            try:
                create_portal_login(self.object, self.request)
            except Exception:
                messages.warning(self.request, "The member was registered, but the portal login couldn't be created "
                                               "or the setup email couldn't be sent. You can retry from this page.")
            else:
                messages.info(self.request, f"A portal sign-in link was emailed to {self.object.email}.")
        return redirect(self.object.get_absolute_url())


class MemberUpdateView(ModulePermissionMixin, PageMetaMixin, UpdateView):
    permission_required = "accounts.manage_members"
    model = Member
    form_class = MemberForm
    template_name = "members/form.html"

    def get_page_title(self):
        return f"Edit {self.object.full_name}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["breadcrumbs"] = members_breadcrumb() + [(self.object.full_name, self.object.get_absolute_url())]
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.changed_data:
            log_activity(ActivityLog.Action.MEMBER_UPDATED,
                         f"Updated member {self.object.member_id} ({', '.join(form.changed_data)})",
                         request=self.request)
        messages.success(self.request, "Member details saved.")
        return response


class MemberActionView(ModulePermissionMixin, View):
    """Base for POST-only state changes on a member."""

    permission_required = "accounts.manage_members"
    http_method_names = ["post"]

    def post(self, request, pk):
        member = get_object_or_404(Member, pk=pk)
        try:
            message = self.perform(member)
        except MemberStateError as exc:
            messages.error(request, str(exc))
        else:
            if message:
                messages.success(request, message)
        return redirect(member.get_absolute_url())


class MemberDeactivateView(MemberActionView):
    def perform(self, member):
        form = MemberDeactivateForm(self.request.POST)
        if not form.is_valid():
            raise MemberStateError("Please give a reason for deactivating this member.")
        deactivate_member(member, self.request.user, form.cleaned_data["reason"], self.request)
        return f"{member.full_name} has been deactivated. Their history has been kept."


class MemberReactivateView(MemberActionView):
    def perform(self, member):
        reactivate_member(member, self.request.user, self.request)
        return f"{member.full_name} has been reactivated."


class MemberPortalLoginView(MemberActionView):
    def perform(self, member):
        try:
            create_portal_login(member, self.request)
        except MemberStateError:
            raise
        except Exception:
            raise MemberStateError("The setup email couldn't be sent. Please check the email settings and try again.")
        return f"A portal sign-in link was emailed to {member.email}."


class MemberPhotoView(View):
    """Serves profile photos only to staff who can view members, or to the member themself."""

    def get(self, request, pk):
        member = get_object_or_404(Member, pk=pk)
        is_self = member.user_id is not None and member.user_id == request.user.pk
        if not (request.user.has_perm("accounts.view_members") or is_self):
            raise PermissionDenied
        if not member.photo:
            raise Http404
        try:
            handle = member.photo.open("rb")
        except FileNotFoundError:
            raise Http404
        content_type = mimetypes.guess_type(member.photo.name)[0] or "application/octet-stream"
        response = FileResponse(handle, content_type=content_type)
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Content-Type-Options"] = "nosniff"
        return response
