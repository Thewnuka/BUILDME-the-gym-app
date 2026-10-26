from django.contrib import admin

from .models import Attendance


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("member", "checked_in_at", "checked_out_at", "auto_closed", "method")
    list_filter = ("method", "auto_closed")
    search_fields = ("member__member_id", "member__full_name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
