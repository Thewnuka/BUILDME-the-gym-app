from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.models import ActivityLog, Notification
from apps.core.notify import notify_staff
from apps.core.utils import log_activity
from apps.expenses.models import Expense, ExpenseCategory
from apps.expenses.services import create_linked_expense

from .models import InventoryItem, StockMovement


class StockError(Exception):
    pass


def _check_low_stock(item):
    """Alert inventory managers once per drop below minimum; re-arm when stock recovers."""
    if item.is_low and item.low_stock_alerted_at is None:
        item.low_stock_alerted_at = timezone.now()
        item.save(update_fields=["low_stock_alerted_at"])
        notify_staff("accounts.manage_inventory", title=f"Low stock: {item.name}",
                     message=f"{item.quantity.normalize():f} {item.unit} left (minimum {item.minimum_stock.normalize():f}).",
                     link=item.get_absolute_url(), category=Notification.Category.INVENTORY,
                     dedupe_key=f"low_stock:{item.pk}:{item.low_stock_alerted_at:%Y%m%d%H%M%S%f}")
    elif not item.is_low and item.low_stock_alerted_at is not None:
        item.low_stock_alerted_at = None
        item.save(update_fields=["low_stock_alerted_at"])


@transaction.atomic
def record_movement(item, kind, quantity, actor, *, unit_cost=None, supplier=None, reference="", reason="",
                    request=None):
    """
    Apply one stock movement. ``quantity`` is positive for purchase/usage/sale;
    for adjustments it is the signed change. The item row is locked, so
    concurrent movements can't oversell or drive stock negative.
    """
    item = InventoryItem.objects.select_for_update().get(pk=item.pk)
    quantity = Decimal(quantity)
    if kind not in StockMovement.Kind.values:
        raise StockError("Unknown movement type.")
    if not item.is_active and kind != StockMovement.Kind.ADJUSTMENT:
        raise StockError("This item is inactive.")
    if kind == StockMovement.Kind.ADJUSTMENT:
        change = quantity
        if not (reason or "").strip():
            raise StockError("Adjustments need a reason (e.g. stock count, damaged, expired).")
    else:
        if quantity <= 0:
            raise StockError("The quantity must be greater than zero.")
        change = quantity if kind == StockMovement.Kind.PURCHASE else -quantity
    if change == 0:
        raise StockError("The quantity change can't be zero.")
    after = item.quantity + change
    if after < 0:
        raise StockError(f"Not enough stock: only {item.quantity.normalize():f} {item.unit} available.")
    total = None
    if kind == StockMovement.Kind.PURCHASE and unit_cost is not None:
        if unit_cost < 0:
            raise StockError("The unit cost can't be negative.")
        total = (Decimal(unit_cost) * quantity).quantize(Decimal("0.01"))
    movement = StockMovement(item=item, kind=kind, quantity_change=change, quantity_after=after, unit_cost=unit_cost,
                             total_cost=total, supplier=supplier, reference=reference[:64], reason=reason[:255],
                             recorded_by=actor)
    if kind == StockMovement.Kind.PURCHASE and total:
        movement.expense = create_linked_expense(
            source=Expense.Source.STOCK,
            category=ExpenseCategory.CLEANING if item.category == InventoryItem.Category.CLEANING
            else ExpenseCategory.SUPPLIES,
            amount=total, expense_date=timezone.localdate(), supplier=supplier or item.supplier,
            invoice_number=reference, description=f"Stock purchase: {quantity.normalize():f} {item.unit} {item.name}",
            actor=actor,
        )
    movement.save()
    item.quantity = after
    item.save(update_fields=["quantity", "updated_at"])
    _check_low_stock(item)
    log_activity(ActivityLog.Action.STOCK_MOVEMENT,
                 f"{item.sku}: {movement.get_kind_display()} {change.normalize():+f} {item.unit} → {after.normalize():f}",
                 actor=actor, request=request)
    return movement


def low_stock_items():
    from django.db.models import F

    return InventoryItem.objects.filter(is_active=True, quantity__lte=F("minimum_stock"))


def sweep_low_stock():
    """Scheduled safety net (e.g. minimum raised without a movement)."""
    sent = 0
    for item in low_stock_items().filter(low_stock_alerted_at__isnull=True):
        _check_low_stock(item)
        sent += 1
    return sent
