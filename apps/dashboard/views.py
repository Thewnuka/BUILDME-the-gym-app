from django.utils import timezone
from django.views.generic import TemplateView

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.core.mixins import PageMetaMixin
from apps.core.models import ActivityLog

from . import registry


class DashboardView(PageMetaMixin, TemplateView):
    """
    Staff with ``view_dashboard`` get the administrative dashboard; every
    widget is additionally filtered by its own module permission. Everyone
    else (e.g. members) gets a personal dashboard limited to their own data.
    """

    def is_admin_dashboard(self):
        return self.request.user.has_perm("accounts.view_dashboard")

    def get_template_names(self):
        return ["dashboard/admin.html" if self.is_admin_dashboard() else "dashboard/member.html"]

    def get_page_title(self):
        return "Dashboard" if self.is_admin_dashboard() else "My Dashboard"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["today"] = timezone.localdate()
        # First-run hint: without an administrator, only member features exist.
        context["setup_required"] = not User.objects.filter(role=Role.SUPER_ADMIN, is_active=True).exists()
        from apps.announcements.services import visible_to

        context["announcements"] = visible_to(user)[:3]
        if self.is_admin_dashboard():
            context["stats"] = registry.stats_for(user)
            context["panels"] = registry.panels_for(user)
        else:
            context["activities"] = ActivityLog.objects.filter(actor=user)[:8]
            # A member only ever sees their own profile and memberships.
            profile = getattr(user, "member_profile", None)
            context["profile"] = profile
            if profile:
                history = list(profile.memberships.order_by("-start_date", "-pk")[:6])
                context["my_memberships"] = history
                context["my_current"] = next(
                    (m for m in history if m.status in ("active", "suspended")), None
                )
                context["my_next"] = next((m for m in history if m.status == "scheduled"), None)
                from apps.appointments.models import Appointment

                context["next_session"] = (Appointment.objects.upcoming().filter(member=profile)
                                           .select_related("trainer__user").first())
                context["current_plan"] = profile.workout_plans.current().first()
        return context
