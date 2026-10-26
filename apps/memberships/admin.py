from django.contrib import admin

from .models import Membership, MembershipEvent, MembershipPlan


class ReadOnlyAdmin(admin.ModelAdmin):
    """History is changed only through the application's lifecycle rules."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MembershipPlan)
class MembershipPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "duration_value", "duration_unit", "price", "is_active")
    list_filter = ("is_active", "duration_unit")
    search_fields = ("name",)

    def has_delete_permission(self, request, obj=None):
        return False


class MembershipEventInline(admin.TabularInline):
    model = MembershipEvent
    extra = 0
    can_delete = False
    readonly_fields = ("event_type", "note", "performed_by", "created_at")


@admin.register(Membership)
class MembershipAdmin(ReadOnlyAdmin):
    list_display = ("member", "plan_name", "start_date", "end_date", "state", "price")
    list_filter = ("state", "kind")
    search_fields = ("member__member_id", "member__full_name", "plan_name")
    inlines = [MembershipEventInline]
