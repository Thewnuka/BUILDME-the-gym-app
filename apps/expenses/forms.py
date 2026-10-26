from decimal import Decimal

from django import forms
from django.conf import settings
from django.utils import timezone

from apps.core.forms import BootstrapFormMixin
from apps.core.uploads import validate_upload
from apps.members.forms import DateInput
from apps.payments.services import looks_like_card_number

from .models import Expense, Supplier


class ExpenseForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["category", "amount", "expense_date", "description", "supplier", "invoice_number", "receipt"]
        widgets = {
            "expense_date": DateInput(),
            "amount": forms.NumberInput(attrs={"min": "0.01", "step": "0.01"}),
            "receipt": forms.ClearableFileInput(attrs={"accept": "application/pdf,image/jpeg,image/png,image/webp"}),
        }
        help_texts = {"receipt": "Optional scan or photo: PDF, JPEG, PNG or WebP."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True)
        self.fields["expense_date"].widget.attrs["max"] = timezone.localdate().isoformat()
        if not self.instance.pk:
            self.fields["expense_date"].initial = timezone.localdate()

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount is None or amount <= 0:
            raise forms.ValidationError("The amount must be greater than zero.")
        if amount > Decimal("99999999.99"):
            raise forms.ValidationError("The amount is too large.")
        return amount

    def clean_expense_date(self):
        day = self.cleaned_data["expense_date"]
        if day > timezone.localdate():
            raise forms.ValidationError("The expense date can't be in the future.")
        return day

    def clean_description(self):
        text = self.cleaned_data["description"]
        if looks_like_card_number(text):
            raise forms.ValidationError("Don't enter card numbers.")
        return text

    def clean_receipt(self):
        return validate_upload(self.cleaned_data.get("receipt"), settings.EXPENSE_RECEIPT_MAX_BYTES, allow_pdf=True)


class SupplierForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "contact_name", "phone", "email", "address", "notes", "is_active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        qs = Supplier.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A supplier with this name already exists.")
        return name
