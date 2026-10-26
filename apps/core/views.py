from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponseServerError
from django.shortcuts import render
from django.template import loader
from django.urls import reverse
from django.views.generic import UpdateView

from apps.core import gym
from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.core.models import ActivityLog
from apps.core.utils import log_activity


@login_not_required
def bad_request(request, exception=None):
    return render(request, "errors/400.html", status=400)


@login_not_required
def permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)


@login_not_required
def page_not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


@login_not_required
def server_error(request):
    # Rendered without request context so a failing database or context
    # processor cannot break the error page, and no details are exposed.
    return HttpResponseServerError(loader.get_template("errors/500.html").render())


class GymSettingsView(ModulePermissionMixin, PageMetaMixin, UpdateView):
    """Business details and reminder periods (blank = server default)."""

    permission_required = "accounts.manage_settings"
    template_name = "core/gym_settings.html"
    page_title = "Gym Settings"

    def get_object(self, queryset=None):
        return gym.get_settings()

    def get_form_class(self):
        from .forms import GymSettingsForm

        return GymSettingsForm

    def get_form_kwargs(self):
        defaults = {
            "gym_name": settings.GYM_NAME,
            "membership_expiry_reminder_days": ",".join(str(d) for d in settings.MEMBERSHIP_EXPIRY_REMINDER_DAYS),
            "payment_due_reminder_days": settings.PAYMENT_DUE_REMINDER_DAYS,
            "expiring_soon_days": settings.MEMBERSHIP_EXPIRING_SOON_DAYS,
            "equipment_maintenance_reminder_days": settings.EQUIPMENT_MAINTENANCE_REMINDER_DAYS,
            "appointment_reminder_hours": settings.APPOINTMENT_REMINDER_HOURS,
        }
        return {**super().get_form_kwargs(), "defaults": defaults}

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        response = super().form_valid(form)
        changed = ", ".join(form.changed_data) or "no changes"
        log_activity(ActivityLog.Action.SETTINGS_UPDATED, f"Gym settings updated ({changed})"[:250],
                     actor=self.request.user, request=self.request)
        messages.success(self.request, "Gym settings saved.")
        return response

    def get_success_url(self):
        return reverse("settings")
