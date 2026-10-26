from django.contrib import admin

from .models import Expense, Supplier

admin.site.register(Supplier)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("reference", "expense_date", "category", "amount", "source", "status")
    list_filter = ("category", "source", "status")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
