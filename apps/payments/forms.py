import uuid
from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.core.forms import BootstrapFormMixin
from apps.members.forms import DateInput

from .models import BalanceAdjustment, PaymentMethod

MONEY_ATTRS = {"min": "0.01", "step": "0.01", "inputmode": "decimal"}


class _TokenMixin(forms.Form):
    """Hidden one-time token: resubmitting the same form can't record twice."""

    submission_token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault("submission_token", uuid.uuid4())


class PaymentForm(BootstrapFormMixin, _TokenMixin):
    amount = forms.DecimalField(label="Amount (LKR)", max_digits=10, decimal_places=2, min_value=Decimal("0.01"),
                                widget=forms.NumberInput(attrs=MONEY_ATTRS))
    method = forms.ChoiceField(label="Payment method", choices=PaymentMethod.choices)
    paid_on = forms.DateField(label="Payment date", widget=DateInput())
    external_reference = forms.CharField(
        label="Bank / card reference", max_length=64, required=False,
        help_text="Bank transfer reference or card approval code. Never enter a card number.",
    )
    pending = forms.BooleanField(
        label="Bank transfer not yet confirmed (record as pending)", required=False,
        help_text="Pending transfers aren't counted as collected until confirmed.",
    )
    notes = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, payable=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paid_on"].initial = timezone.localdate()
        self.fields["paid_on"].widget.attrs["max"] = timezone.localdate().isoformat()
        if payable is not None:
            self.fields["amount"].initial = payable
            self.fields["amount"].widget.attrs["max"] = str(payable)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("pending") and cleaned.get("method") != PaymentMethod.BANK_TRANSFER:
            self.add_error("pending", "Only bank transfers can be recorded as pending.")
        return cleaned


class RefundForm(BootstrapFormMixin, _TokenMixin):
    amount = forms.DecimalField(label="Refund amount (LKR)", max_digits=10, decimal_places=2,
                                min_value=Decimal("0.01"), widget=forms.NumberInput(attrs=MONEY_ATTRS))
    method = forms.ChoiceField(label="Refund method", choices=PaymentMethod.choices)
    paid_on = forms.DateField(label="Refund date", widget=DateInput())
    reason = forms.CharField(max_length=200)

    def __init__(self, *args, refundable=None, method=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paid_on"].initial = timezone.localdate()
        if refundable is not None:
            self.fields["amount"].initial = refundable
            self.fields["amount"].widget.attrs["max"] = str(refundable)
        if method:
            self.fields["method"].initial = method


class AdjustmentForm(BootstrapFormMixin, forms.Form):
    kind = forms.ChoiceField(label="Type", choices=BalanceAdjustment.Kind.choices, widget=forms.RadioSelect,
                             initial=BalanceAdjustment.Kind.DISCOUNT)
    amount = forms.DecimalField(label="Amount (LKR)", max_digits=10, decimal_places=2, min_value=Decimal("0.01"),
                                widget=forms.NumberInput(attrs=MONEY_ATTRS))
    reason = forms.CharField(max_length=200, widget=forms.TextInput(
        attrs={"placeholder": "e.g. Loyalty discount approved by manager"}))
