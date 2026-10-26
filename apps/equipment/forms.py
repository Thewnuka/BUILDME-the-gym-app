from django import forms
from django.utils import timezone

from apps.core.forms import BootstrapFormMixin
from apps.expenses.models import Supplier
from apps.members.forms import DateInput

from .models import Equipment, FaultReport, MaintenanceRecord


class EquipmentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Equipment
        fields = ["code", "name", "category", "brand_model", "serial_number", "location", "purchase_date",
                  "purchase_cost", "supplier", "warranty_expires", "warranty_notes", "condition", "status",
                  "maintenance_interval_days", "next_maintenance_date", "notes"]
        widgets = {"purchase_date": DateInput(), "warranty_expires": DateInput(), "next_maintenance_date": DateInput(),
                   "notes": forms.Textarea(attrs={"rows": 2}),
                   "purchase_cost": forms.NumberInput(attrs={"min": "0", "step": "0.01"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].required = False
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True)
        if self.instance.pk:
            # The purchase is already booked as an expense; cost changes go through Expenses.
            self.fields["purchase_cost"].disabled = bool(self.instance.purchase_expense_id)

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        if code and Equipment.objects.filter(code__iexact=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This equipment code is already used.")
        return code

    def clean(self):
        cleaned = super().clean()
        purchase, warranty = cleaned.get("purchase_date"), cleaned.get("warranty_expires")
        if purchase and purchase > timezone.localdate():
            self.add_error("purchase_date", "The purchase date can't be in the future.")
        if purchase and warranty and warranty < purchase:
            self.add_error("warranty_expires", "The warranty can't end before the purchase date.")
        cost = cleaned.get("purchase_cost")
        if cost is not None and cost < 0:
            self.add_error("purchase_cost", "The cost can't be negative.")
        return cleaned


class FaultForm(BootstrapFormMixin, forms.ModelForm):
    take_out_of_service = forms.BooleanField(label="Take it out of service now", required=False)

    class Meta:
        model = FaultReport
        fields = ["severity", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3, "placeholder": "What's wrong? e.g. belt slipping at speed 8+"})}


class MaintenanceForm(BootstrapFormMixin, forms.ModelForm):
    status_after = forms.ChoiceField(label="Status afterwards", choices=Equipment.Status.choices,
                                     initial=Equipment.Status.AVAILABLE)
    resolves = forms.ModelChoiceField(label="Fixes reported fault", queryset=FaultReport.objects.none(), required=False)

    class Meta:
        model = MaintenanceRecord
        fields = ["kind", "performed_on", "description", "cost", "performed_by", "supplier"]
        widgets = {"performed_on": DateInput(), "cost": forms.NumberInput(attrs={"min": "0", "step": "0.01"})}
        help_texts = {"cost": "A cost is recorded once, as a linked Maintenance expense."}

    def __init__(self, *args, equipment=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True)
        if equipment is not None:
            self.fields["resolves"].queryset = equipment.faults.filter(status=FaultReport.Status.OPEN)
            self.fields["resolves"].label_from_instance = lambda f: f"{f.created_at:%d %b}: {f.description[:60]}"

    def clean_performed_on(self):
        day = self.cleaned_data["performed_on"]
        if day > timezone.localdate():
            raise forms.ValidationError("Maintenance can't be recorded in the future.")
        return day

    def clean_cost(self):
        cost = self.cleaned_data.get("cost")
        if cost is not None and cost < 0:
            raise forms.ValidationError("The cost can't be negative.")
        return cost
