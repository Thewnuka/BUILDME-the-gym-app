from django.contrib import admin

from .models import Appointment, SessionType


@admin.register(SessionType)
class SessionTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "duration_minutes", "fee", "is_active")


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("starts_at", "trainer", "member", "session_type", "status")
    list_filter = ("status",)
    raw_id_fields = ("trainer", "member")

    def has_change_permission(self, request, obj=None):
        return False  # changes go through the booking rules
