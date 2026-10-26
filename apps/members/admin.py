from django.contrib import admin

from .models import Member


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("member_id", "full_name", "phone", "email", "status", "joined_on")
    list_filter = ("status",)
    search_fields = ("member_id", "full_name", "phone", "email")
    readonly_fields = ("member_id", "created_at", "updated_at", "created_by")
    raw_id_fields = ("user",)

    def has_delete_permission(self, request, obj=None):
        return False  # members are deactivated, never deleted
