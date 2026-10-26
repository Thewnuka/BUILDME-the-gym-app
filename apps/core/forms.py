from django import forms


class BootstrapFormMixin:
    """Applies Bootstrap 5 classes to widgets and flags invalid fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_bootstrap()

    def apply_bootstrap(self):
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
                css = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css = "form-select"
            else:
                css = "form-control"
            classes = widget.attrs.get("class", "")
            if css not in classes:
                classes = f"{classes} {css}".strip()
            # Read the cached errors only: touching `self.errors` here would
            # trigger validation before subclasses finish initialising.
            if self._errors and name in self._errors:
                classes += " is-invalid"
            widget.attrs["class"] = classes

    def full_clean(self):
        super().full_clean()
        if self.is_bound:
            self.apply_bootstrap()


class GymSettingsForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        from apps.core.models import GymSettings

        model = GymSettings
        exclude = ["updated_by"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, defaults=None, **kwargs):
        super().__init__(*args, **kwargs)
        for name, value in (defaults or {}).items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = f"Default: {value}"
        for name in ("payment_due_reminder_days", "equipment_maintenance_reminder_days", "appointment_reminder_hours",
                     "expiring_soon_days"):
            self.fields[name].widget.attrs.update(min=1, max=365)

    def clean_membership_expiry_reminder_days(self):
        raw = self.cleaned_data["membership_expiry_reminder_days"].replace(" ", "")
        if not raw:
            return ""
        parts = raw.split(",")
        if not all(p.isdigit() and 1 <= int(p) <= 90 for p in parts):
            raise forms.ValidationError("Enter whole numbers from 1 to 90 separated by commas, e.g. 7,1.")
        return ",".join(str(d) for d in sorted({int(p) for p in parts}, reverse=True))

    def _bounded(self, name, high):
        value = self.cleaned_data.get(name)
        if value is not None and not 1 <= value <= high:
            raise forms.ValidationError(f"Enter a number from 1 to {high}.")
        return value

    def clean_payment_due_reminder_days(self):
        return self._bounded("payment_due_reminder_days", 60)

    def clean_expiring_soon_days(self):
        return self._bounded("expiring_soon_days", 90)

    def clean_equipment_maintenance_reminder_days(self):
        return self._bounded("equipment_maintenance_reminder_days", 90)

    def clean_appointment_reminder_hours(self):
        return self._bounded("appointment_reminder_hours", 168)
