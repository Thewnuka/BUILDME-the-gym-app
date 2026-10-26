from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.core.views import GymSettingsView

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("members/", include("apps.members.urls")),
    path("memberships/", include("apps.memberships.urls")),
    path("payments/", include("apps.payments.urls")),
    path("attendance/", include("apps.attendance.urls")),
    path("trainers/", include("apps.trainers.urls")),
    path("appointments/", include("apps.appointments.urls")),
    path("workouts/", include("apps.workouts.urls")),
    path("nutrition/", include("apps.nutrition.urls")),
    path("notifications/", include("apps.core.urls")),
    path("expenses/", include("apps.expenses.urls")),
    path("equipment/", include("apps.equipment.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("announcements/", include("apps.announcements.urls")),
    path("reports/", include("apps.reports.urls")),
    path("settings/", GymSettingsView.as_view(), name="settings"),
    path("", include("apps.dashboard.urls")),
]

# Uploaded files (member photos, certificates, expense receipts, exercise images) are
# never served from MEDIA_URL: each has a view that checks the viewer's permissions.

handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"
