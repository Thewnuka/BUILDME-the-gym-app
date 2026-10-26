from django.contrib import admin

from .models import BalanceAdjustment, Payment


class ReadOnlyAdmin(admin.ModelAdmin):
    """Financial records change only through the application's audited workflows."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("reference", "receipt_number", "kind", "status", "member", "amount", "method", "paid_on")
    list_filter = ("kind", "status", "method")
    search_fields = ("reference", "receipt_number", "member__member_id", "member__full_name")


@admin.register(BalanceAdjustment)
class BalanceAdjustmentAdmin(ReadOnlyAdmin):
    list_display = ("membership", "kind", "amount", "reason", "created_by", "created_at")
