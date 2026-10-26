from decimal import Decimal

from django import forms

from apps.core.forms import BootstrapFormMixin
from apps.expenses.models import Supplier

from .models import InventoryItem, StockMovement


class ItemForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ["sku", "name", "category", "unit", "minimum_stock", "unit_cost", "supplier", "is_active"]
        widgets = {"minimum_stock": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
                   "unit_cost": forms.NumberInput(attrs={"min": "0", "step": "0.01"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True)

    def clean_sku(self):
        sku = self.cleaned_data["sku"].strip().upper()
        if InventoryItem.objects.filter(sku__iexact=sku).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This SKU is already used.")
        return sku

    def clean_minimum_stock(self):
        value = self.cleaned_data["minimum_stock"]
        if value is None or value < 0:
            raise forms.ValidationError("The minimum can't be negative.")
        return value


class MovementForm(BootstrapFormMixin, forms.Form):
    kind = forms.ChoiceField(choices=StockMovement.Kind.choices)
    quantity = forms.DecimalField(max_digits=10, decimal_places=2,
                                  help_text="For adjustments, use a negative number to reduce stock.")
    unit_cost = forms.DecimalField(label="Unit cost (LKR)", max_digits=10, decimal_places=2, required=False,
                                   min_value=Decimal("0"), help_text="Purchases only. Creates a linked expense.")
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none(), required=False)
    reference = forms.CharField(label="Invoice / reference", max_length=64, required=False)
    reason = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, allowed_kinds=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True)
        if allowed_kinds is not None:
            self.fields["kind"].choices = [c for c in StockMovement.Kind.choices if c[0] in allowed_kinds]
