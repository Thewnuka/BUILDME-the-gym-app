from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.core.utils import log_activity

from .models import Expense


class ExpenseError(Exception):
    pass


def create_linked_expense(*, source, category, amount, expense_date, description, actor, supplier=None,
                          invoice_number=""):
    """
    Create the single Expense row for a cost recorded elsewhere (repair,
    equipment purchase, stock purchase). Returns None for zero/blank costs.
    Callers run this inside their own transaction, so both rows are saved
    together or not at all.
    """
    if amount is None or Decimal(amount) <= 0:
        return None
    expense = Expense.objects.create(
        source=source, category=category, amount=amount, expense_date=expense_date,
        description=description[:255], supplier=supplier, invoice_number=invoice_number[:64], recorded_by=actor,
    )
    log_activity(ActivityLog.Action.EXPENSE_RECORDED,
                 f"{expense.reference}: {expense.get_category_display()} LKR {expense.amount:,.2f} "
                 f"({expense.get_source_display().lower()})", actor=actor)
    return expense


@transaction.atomic
def void_expense(expense, actor, reason, request=None):
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    reason = (reason or "").strip()
    if not reason:
        raise ExpenseError("A reason is required to void an expense.")
    if expense.status == Expense.Status.VOIDED:
        raise ExpenseError("This expense is already void.")
    expense.status = Expense.Status.VOIDED
    expense.void_reason = reason[:255]
    expense.voided_by = actor
    expense.voided_at = timezone.now()
    expense.save(update_fields=["status", "void_reason", "voided_by", "voided_at", "updated_at"])
    log_activity(ActivityLog.Action.EXPENSE_VOIDED, f"{expense.reference} voided: {reason[:120]}", actor=actor,
                 request=request)
    return expense
