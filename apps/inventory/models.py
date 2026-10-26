"""
Inventory. Quantities change only through StockMovement rows (append-only
ledger): purchase (+), usage/sale (−) and adjustment (±). Each movement
stores the resulting quantity, and both the item and the movement have
database checks that stock can never go negative.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.urls import reverse


class InventoryItem(models.Model):
    class Category(models.TextChoices):
        SUPPLEMENTS = "supplements", "Supplements"
        DRINKS = "drinks", "Drinks"
        CLEANING = "cleaning", "Cleaning supplies"
        TOWELS = "towels", "Towels & linen"
        MERCHANDISE = "merchandise", "Merchandise"
        CONSUMABLES = "consumables", "Gym consumables (chalk, wipes…)"
        OFFICE = "office", "Office supplies"
        OTHER = "other", "Other"

    sku = models.CharField("SKU / item code", max_length=40)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=12, choices=Category.choices, db_index=True)
    unit = models.CharField(max_length=20, default="pcs", help_text="e.g. pcs, bottles, kg, litres")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0, editable=False)
    minimum_stock = models.DecimalField("minimum stock level", max_digits=10, decimal_places=2, default=0)
    unit_cost = models.DecimalField("typical unit cost (LKR)", max_digits=10, decimal_places=2, null=True, blank=True)
    supplier = models.ForeignKey("expenses.Supplier", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="inventory_items")
    is_active = models.BooleanField(default=True)
    # Set when a low-stock alert was sent; cleared when stock recovers, so
    # each drop below the minimum alerts exactly once.
    low_stock_alerted_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("sku"), name="inventory_unique_sku_ci"),
            models.CheckConstraint(condition=Q(quantity__gte=0), name="inventory_quantity_not_negative"),
            models.CheckConstraint(condition=Q(minimum_stock__gte=0), name="inventory_minimum_not_negative"),
        ]

    def __str__(self):
        return f"{self.sku} · {self.name}"

    def get_absolute_url(self):
        return reverse("inventory:detail", args=[self.pk])

    @property
    def is_low(self):
        return self.is_active and self.quantity <= self.minimum_stock


class StockMovement(models.Model):
    class Kind(models.TextChoices):
        PURCHASE = "purchase", "Purchase / received"
        USAGE = "usage", "Used"
        SALE = "sale", "Sold"
        ADJUSTMENT = "adjustment", "Adjustment (count correction, damage…)"

    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    quantity_change = models.DecimalField(max_digits=10, decimal_places=2)
    quantity_after = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    supplier = models.ForeignKey("expenses.Supplier", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    reference = models.CharField("invoice / reference", max_length=64, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    expense = models.OneToOneField("expenses.Expense", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="stock_movement", editable=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(quantity_change=0), name="movement_change_not_zero"),
            models.CheckConstraint(condition=Q(quantity_after__gte=0), name="movement_after_not_negative"),
            models.CheckConstraint(condition=~Q(kind="purchase") | Q(quantity_change__gt=0),
                                   name="movement_purchase_positive"),
            models.CheckConstraint(condition=~Q(kind__in=["usage", "sale"]) | Q(quantity_change__lt=0),
                                   name="movement_usage_negative"),
            models.CheckConstraint(condition=Q(total_cost__isnull=True) | Q(total_cost__gte=0),
                                   name="movement_cost_not_negative"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.quantity_change:+} {self.item.sku}"

    @property
    def quantity_before(self):
        return self.quantity_after - self.quantity_change


