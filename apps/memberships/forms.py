from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.core.forms import BootstrapFormMixin
from apps.members.forms import DateInput

from . import periods
from .models import DURATION_PRESETS, MAX_DURATION_DAYS, MAX_DURATION_MONTHS, WEEKDAYS, DurationUnit, MembershipPlan

MAX_PRICE = Decimal("9999999.99")


class TimeInput(forms.TimeInput):
    input_type = "time"

    def __init__(self, **kwargs):
        super().__init__(format="%H:%M", **kwargs)


class PlanForm(BootstrapFormMixin, forms.ModelForm):
    allowed_days = forms.MultipleChoiceField(
        label="Allowed days", choices=WEEKDAYS, required=False, widget=forms.CheckboxSelectMultiple,
        help_text="Leave all unticked to allow every day.",
    )
    duration_preset = forms.ChoiceField(
        label="Duration",
        choices=[(k, label) for k, (label, _v, _u) in DURATION_PRESETS.items()] + [("custom", "Custom duration")],
    )

    class Meta:
        model = MembershipPlan
        fields = [
            "name", "description", "duration_preset", "duration_value", "duration_unit", "price",
            "benefits", "access_restrictions", "allowed_days", "access_start_time", "access_end_time", "is_active",
        ]
        labels = {
            "duration_value": "Custom length", "duration_unit": "Unit", "is_active": "Plan is active",
            "access_restrictions": "Access notes", "access_start_time": "Access from", "access_end_time": "Access until",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "benefits": forms.Textarea(attrs={"rows": 4, "placeholder": "Gym floor access\nFree fitness assessment\nLocker"}),
            "access_restrictions": forms.Textarea(attrs={"rows": 2}),
            "price": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "duration_value": forms.NumberInput(attrs={"min": "1"}),
            "access_start_time": TimeInput(),
            "access_end_time": TimeInput(),
        }
        help_texts = {"is_active": "Inactive plans stay on existing memberships but can't be assigned to new ones."}

    def __init__(self, *args, can_change_price=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["duration_value"].required = False
        self.fields["duration_unit"].required = False
        self.fields["duration_preset"].initial = self.instance.duration_preset if self.instance.pk else "monthly"
        self.fields["allowed_days"].initial = sorted(str(d) for d in self.instance.allowed_weekdays)
        self.fields["access_start_time"].help_text = "Optional daily window, checked at check-in (gym local time)."
        if not can_change_price:
            # Disabled fields ignore submitted data, so a crafted POST can't change the price.
            self.fields["price"].disabled = True
            self.fields["price"].help_text = "You don't have permission to change plan prices."

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        qs = MembershipPlan.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A plan with this name already exists.")
        return name

    def clean_price(self):
        price = self.cleaned_data.get("price")
        if price is None:
            return price
        if price < 0:
            raise forms.ValidationError("The price can't be negative.")
        if price > MAX_PRICE:
            raise forms.ValidationError("The price is too large.")
        return price

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("access_start_time"), cleaned.get("access_end_time")
        if bool(start) != bool(end):
            self.add_error("access_end_time" if start else "access_start_time", "Enter both the start and end time.")
        elif start and end and start >= end:
            self.add_error("access_end_time", "The end time must be after the start time (overnight windows aren't supported).")
        days = cleaned.get("allowed_days") or []
        cleaned["access_days"] = "" if len(days) in (0, 7) else ",".join(sorted(days))
        preset = cleaned.get("duration_preset")
        if preset in DURATION_PRESETS:
            _label, value, unit = DURATION_PRESETS[preset]
            cleaned["duration_value"], cleaned["duration_unit"] = value, unit
        elif preset == "custom":
            value, unit = cleaned.get("duration_value"), cleaned.get("duration_unit")
            if not value:
                self.add_error("duration_value", "Enter the plan length.")
            elif not unit:
                self.add_error("duration_unit", "Choose days or months.")
            elif unit == periods.MONTH and not 1 <= value <= MAX_DURATION_MONTHS:
                self.add_error("duration_value", f"Month-based plans must be 1–{MAX_DURATION_MONTHS} months.")
            elif unit == periods.DAY and not 1 <= value <= MAX_DURATION_DAYS:
                self.add_error("duration_value", f"Day-based plans must be 1–{MAX_DURATION_DAYS} days.")
        return cleaned

    def save(self, commit=True):
        plan = super().save(commit=False)
        plan.duration_value = self.cleaned_data["duration_value"]
        plan.duration_unit = self.cleaned_data["duration_unit"]
        plan.access_days = self.cleaned_data["access_days"]
        if commit:
            plan.save()
        return plan


class PlanChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, plan):
        return f"{plan.name} — {plan.duration_display} — LKR {plan.price:,.2f}"


class AssignMembershipForm(BootstrapFormMixin, forms.Form):
    plan = PlanChoiceField(queryset=MembershipPlan.objects.active().order_by("price", "name"), empty_label="Choose a plan…")
    start_date = forms.DateField(widget=DateInput())
    notes = forms.CharField(max_length=255, required=False,
                            widget=forms.TextInput(attrs={"placeholder": "Optional, e.g. promotion or referral"}))

    def __init__(self, *args, default_start=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["start_date"].initial = default_start or timezone.localdate()


class RenewMembershipForm(BootstrapFormMixin, forms.Form):
    plan = PlanChoiceField(queryset=MembershipPlan.objects.active().order_by("price", "name"), empty_label=None)
    notes = forms.CharField(max_length=255, required=False)


class ReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=200)

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if len(reason) < 3:
            raise forms.ValidationError("Please give a short reason.")
        return reason


__all__ = ["PlanForm", "AssignMembershipForm", "RenewMembershipForm", "ReasonForm", "DurationUnit"]
