from django import forms
from django.utils import timezone

from apps.core.forms import BootstrapFormMixin
from apps.members.forms import DateInput

from .models import Meal, MealPlan, NutritionProfile


class MealPlanForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = MealPlan
        fields = ["name", "goal", "description", "start_date", "end_date"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3}), "start_date": DateInput(), "end_date": DateInput()}

    def __init__(self, *args, for_member=False, **kwargs):
        super().__init__(*args, **kwargs)
        if for_member:
            self.fields["start_date"].required = True
            if not self.instance.pk:
                self.fields["start_date"].initial = timezone.localdate()
        else:
            del self.fields["start_date"]
            del self.fields["end_date"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("start_date") and cleaned.get("end_date") and cleaned["end_date"] < cleaned["start_date"]:
            self.add_error("end_date", "The end date must be on or after the start date.")
        return cleaned


class MealForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Meal
        fields = ["day_number", "meal_type", "title", "description", "ingredients"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2}), "ingredients": forms.Textarea(attrs={"rows": 3})}

    def clean_day_number(self):
        n = self.cleaned_data["day_number"]
        if n < 1 or n > 31:
            raise forms.ValidationError("Use a day between 1 and 31.")
        return n


class NutritionProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = NutritionProfile
        fields = ["preferences", "allergies", "goals"]
        widgets = {f: forms.Textarea(attrs={"rows": 3}) for f in ("preferences", "allergies", "goals")}
