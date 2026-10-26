from django import forms
from django.conf import settings
from django.utils import timezone

from apps.core.forms import BootstrapFormMixin
from apps.core.uploads import validate_upload
from apps.members.forms import DateInput

from .models import Exercise, MeasurementType, ProgressEntry, WorkoutDay, WorkoutItem, WorkoutPlan


class ExerciseForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Exercise
        fields = ["name", "muscle_group", "category", "description", "instructions", "image", "video_url", "is_active"]
        widgets = {"instructions": forms.Textarea(attrs={"rows": 5}),
                   "image": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"})}

    def clean_image(self):
        return validate_upload(self.cleaned_data.get("image"), settings.MEMBER_PHOTO_MAX_BYTES)

    def clean_video_url(self):
        url = self.cleaned_data.get("video_url", "")
        if url and not url.lower().startswith("https://"):
            raise forms.ValidationError("Use a secure (https://) link.")
        return url


class PlanForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = WorkoutPlan
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
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The end date must be on or after the start date.")
        return cleaned


class DayForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = WorkoutDay
        fields = ["day_number", "title"]

    def __init__(self, *args, plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan = plan
        if plan is not None and not self.is_bound:
            self.fields["day_number"].initial = (plan.days.order_by("-day_number").values_list("day_number", flat=True)
                                                 .first() or 0) + 1

    def clean_day_number(self):
        n = self.cleaned_data["day_number"]
        if n < 1:
            raise forms.ValidationError("Days start at 1.")
        if self.plan and self.plan.days.filter(day_number=n).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(f"Day {n} already exists in this plan.")
        return n


class ItemForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = WorkoutItem
        fields = ["exercise", "sets", "reps", "weight_kg", "duration_seconds", "rest_seconds", "notes", "order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["exercise"].queryset = Exercise.objects.filter(is_active=True)
        for name in ("sets", "reps", "duration_seconds", "rest_seconds", "weight_kg"):
            self.fields[name].widget.attrs["min"] = "0"

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("sets") and not cleaned.get("duration_seconds"):
            raise forms.ValidationError("Enter sets (and reps) or a duration.")
        if cleaned.get("reps") and not cleaned.get("sets"):
            self.add_error("sets", "Enter the number of sets.")
        weight = cleaned.get("weight_kg")
        if weight is not None and weight < 0:
            self.add_error("weight_kg", "Weight can't be negative.")
        return cleaned


class AssignPlanForm(BootstrapFormMixin, forms.Form):
    member = forms.ModelChoiceField(queryset=None, empty_label="Choose a member…")
    start_date = forms.DateField(widget=DateInput())
    end_date = forms.DateField(widget=DateInput(), required=False)

    def __init__(self, *args, members=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = members.order_by("full_name")
        self.fields["member"].label_from_instance = lambda m: f"{m.full_name} ({m.member_id})"
        self.fields["start_date"].initial = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("start_date") and cleaned.get("end_date") and cleaned["end_date"] < cleaned["start_date"]:
            self.add_error("end_date", "The end date must be on or after the start date.")
        return cleaned


class ProgressForm(BootstrapFormMixin, forms.ModelForm):
    """Body measurements. Values are recorded as given; nothing is interpreted medically."""

    class Meta:
        model = ProgressEntry
        fields = ["recorded_on", "weight_kg", "height_cm", "body_fat_pct", "notes"]
        widgets = {"recorded_on": DateInput(), "notes": forms.Textarea(attrs={"rows": 2})}
        help_texts = {"weight_kg": "20–400 kg", "height_cm": "50–260 cm", "body_fat_pct": "Optional, 2–75 %"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.measurement_types = list(MeasurementType.objects.filter(is_active=True))
        for mt in self.measurement_types:
            self.fields[f"m_{mt.pk}"] = forms.DecimalField(label=f"{mt.name} ({mt.unit})", required=False,
                                                           max_digits=6, decimal_places=1, min_value=0.1)
        self.apply_bootstrap()
        self.fields["recorded_on"].widget.attrs["max"] = timezone.localdate().isoformat()

    def clean_recorded_on(self):
        day = self.cleaned_data["recorded_on"]
        if day > timezone.localdate():
            raise forms.ValidationError("The date can't be in the future.")
        return day

    def clean(self):
        cleaned = super().clean()
        ranges = {"weight_kg": (20, 400), "height_cm": (50, 260), "body_fat_pct": (2, 75)}
        for name, (low, high) in ranges.items():
            value = cleaned.get(name)
            if value is not None and not (low <= value <= high):
                self.add_error(name, f"Enter a value between {low} and {high}.")
        values = [cleaned.get(n) for n in ranges] + [cleaned.get(f"m_{mt.pk}") for mt in self.measurement_types]
        if all(v is None for v in values) and not cleaned.get("notes"):
            raise forms.ValidationError("Enter at least one measurement or a note.")
        return cleaned

    def measurement_values(self):
        return [(mt, self.cleaned_data.get(f"m_{mt.pk}")) for mt in self.measurement_types
                if self.cleaned_data.get(f"m_{mt.pk}") is not None]


class MeasurementTypeForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = MeasurementType
        fields = ["name", "unit"]
