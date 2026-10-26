"""
Operating expenses.

Every cost the gym pays is exactly one Expense row, so financial reports
never double-count:
* manual expenses (rent, bills, payroll totals, …) are entered directly;
* repair costs, equipment purchases and stock purchases create a *linked*
  expense automatically (``source`` says where it came from) that can't be
  edited separately from its source.
Mistakes are voided with a reason, never deleted.
"""
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.urls import reverse


class Supplier(models.Model):
    name = models.CharField(max_length=150)
    contact_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="supplier_unique_name_ci")]

    def __str__(self):
        return self.name


def expense_receipt_path(instance, filename):
    return f"expenses/receipts/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class ExpenseCategory(models.TextChoices):
    RENT = "rent", "Rent"
    ELECTRICITY = "electricity", "Electricity"
    WATER = "water", "Water"
    SALARIES = "salaries", "Salaries & wages"
    EQUIPMENT = "equipment", "Equipment purchases"
    MAINTENANCE = "maintenance", "Maintenance & repairs"
    CLEANING = "cleaning", "Cleaning supplies"
    SUPPLIES = "supplies", "Stock & supplies"
    MARKETING = "marketing", "Marketing"
    INSURANCE = "insurance", "Insurance"
    INTERNET = "internet", "Internet & phone"
    OTHER = "other", "Other operating costs"


# Equipment purchases are capital spending: reports show them separately from
# operating expenses (see apps/reports/finance.py).
CAPITAL_CATEGORIES = {ExpenseCategory.EQUIPMENT}


class Expense(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Entered manually"
        EQUIPMENT = "equipment", "Equipment purchase"
        MAINTENANCE = "maintenance", "Maintenance / repair"
        STOCK = "stock", "Stock purchase"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        VOIDED = "voided", "Voided"

    reference = models.CharField(max_length=20, unique=True, null=True, editable=False)
    category = models.CharField(max_length=15, choices=ExpenseCategory.choices, db_index=True)
    amount = models.DecimalField("amount (LKR)", max_digits=12, decimal_places=2)
    expense_date = models.DateField(db_index=True)
    description = models.CharField(max_length=255)
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="expenses")
    invoice_number = models.CharField("supplier invoice / bill no.", max_length=64, blank=True)
    receipt = models.FileField(upload_to=expense_receipt_path, blank=True)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.MANUAL, editable=False)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    void_reason = models.CharField(max_length=255, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    voided_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-expense_date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="expense_amount_positive"),
            models.CheckConstraint(condition=~Q(status="voided") | (Q(voided_at__isnull=False) & ~Q(void_reason="")),
                                   name="expense_void_has_reason"),
        ]

    def __str__(self):
        return f"{self.reference or 'new'} · {self.get_category_display()} LKR {self.amount:,.2f}"

    def get_absolute_url(self):
        return reverse("expenses:detail", args=[self.pk])

    @property
    def is_linked(self):
        return self.source != self.Source.MANUAL

    @property
    def is_capital(self):
        return self.category in CAPITAL_CATEGORIES

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"EXP-{self.pk:06d}"
            type(self).objects.filter(pk=self.pk).update(reference=self.reference)
