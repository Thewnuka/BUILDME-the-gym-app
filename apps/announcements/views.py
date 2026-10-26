from datetime import timedelta

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from apps.core.forms import BootstrapFormMixin
from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.memberships.models import MembershipPlan

from . import services
from .models import Announcement


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%dT%H:%M", **kwargs)


class AnnouncementForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ["title", "kind", "body", "audience", "plan", "role", "publish_at", "expires_at",
                  "notify_recipients", "send_external", "is_active"]
        widgets = {"body": forms.Textarea(attrs={"rows": 5}), "publish_at": DateTimeInput(),
                   "expires_at": DateTimeInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = MembershipPlan.objects.filter(is_active=True)
        self.fields["plan"].required = False
        self.fields["role"].required = False
        if not self.instance.pk:
            self.fields["publish_at"].initial = timezone.localtime().replace(second=0, microsecond=0)

    def clean(self):
        cleaned = super().clean()
        audience = cleaned.get("audience")
        if audience == Announcement.Audience.PLAN and not cleaned.get("plan"):
            self.add_error("plan", "Choose the plan whose members should see this.")
        if audience == Announcement.Audience.ROLE and not cleaned.get("role"):
            self.add_error("role", "Choose the staff role.")
        if audience != Announcement.Audience.PLAN:
            cleaned["plan"] = None
        if audience != Announcement.Audience.ROLE:
            cleaned["role"] = ""
        publish, expires = cleaned.get("publish_at"), cleaned.get("expires_at")
        if publish and expires and expires <= publish:
            self.add_error("expires_at", "The expiry must be after the publish time.")
        if expires and expires <= timezone.now() - timedelta(minutes=1) and not self.instance.pk:
            self.add_error("expires_at", "The expiry is already in the past.")
        return cleaned


class ManageMixin(ModulePermissionMixin):
    permission_required = "accounts.manage_announcements"


class AnnouncementAdminListView(ManageMixin, PageMetaMixin, ListView):
    model = Announcement
    template_name = "announcements/manage_list.html"
    context_object_name = "announcements"
    paginate_by = 30
    page_title = "Manage Announcements"


class AnnouncementCreateView(ManageMixin, PageMetaMixin, CreateView):
    form_class = AnnouncementForm
    template_name = "announcements/form.html"
    page_title = "New Announcement"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        sent = services.publish_if_due(self.object, self.request.user)
        if self.object.state == "Scheduled":
            messages.success(self.request, "Announcement scheduled. Recipients are notified when it goes live.")
        else:
            messages.success(self.request, f"Announcement published. {sent} notification(s) created.")
        return response


class AnnouncementUpdateView(ManageMixin, PageMetaMixin, UpdateView):
    model = Announcement
    form_class = AnnouncementForm
    template_name = "announcements/form.html"
    page_title = "Edit Announcement"

    def form_valid(self, form):
        response = super().form_valid(form)
        services.publish_if_due(self.object, self.request.user)  # only notifies once, ever
        messages.success(self.request, "Announcement saved. Already-notified recipients aren't notified again.")
        return response


class AnnouncementToggleView(ManageMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        a = get_object_or_404(Announcement, pk=pk)
        a.is_active = not a.is_active
        a.save(update_fields=["is_active", "updated_at"])
        if a.is_active:
            services.publish_if_due(a, request.user)
        messages.success(request, "Announcement activated." if a.is_active else "Announcement deactivated.")
        return redirect("announcements:manage")


class AnnouncementBoardView(PageMetaMixin, TemplateView):
    """Live announcements for the signed-in user."""

    template_name = "announcements/board.html"
    page_title = "Announcements"

    def get_context_data(self, **kwargs):
        return super().get_context_data(announcements=services.visible_to(self.request.user), **kwargs)


class AnnouncementDetailView(PageMetaMixin, DetailView):
    model = Announcement
    template_name = "announcements/detail.html"
    context_object_name = "announcement"

    def get_object(self, queryset=None):
        a = super().get_object(queryset)
        user = self.request.user
        if user.has_perm("accounts.manage_announcements"):
            return a
        if a not in services.visible_to(user, Announcement.objects.filter(pk=a.pk)):
            raise PermissionDenied
        return a

    def get_page_title(self):
        return self.object.title
