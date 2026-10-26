from apps.dashboard.registry import register

from .services import low_stock_items


@register("low_stock")
def low_stock(user):
    return {"value": low_stock_items().count(), "note": "At or below minimum stock"}
