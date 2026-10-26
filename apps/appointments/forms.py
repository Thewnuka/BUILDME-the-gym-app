from datetime import datetime

from django import forms
from django.utils import timezone

from apps.core.forms import BootstrapFormMixin
from apps.members.forms import DateInput
from apps.members.models import Member
from apps.trainers.models import TrainerProfile

from .models import SessionType


class SlotSearchForm(BootstrapFormMixin, forms.Form):
    """GET form: pick trainer, session type and day to list free slots."""

    member = forms.ModelChoiceField(queryset=Member.objects.none(), required=False, empty_label="Choose a member…")
    trainer = forms.ModelChoiceField(queryset=TrainerProfile.objects.none(), empty_label="Choose a trainer…")
    session_type = forms.ModelChoiceField(queryset=SessionType.objects.filter(is_active=True), empty_label=None,
                                          label="Session")
    day = forms.DateField(widget=DateInput(), label="Date")

    def __init__(self, *args, trainers=None, with_member=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["trainer"].queryset = trainers if trainers is not None else TrainerProfile.objects.active()
        self.fields["trainer"].label_from_instance = lambda t: t.name
        self.fields["session_type"].label_from_instance = (
            lambda s: f"{s.name} · {s.duration_minutes} min" + (f" · LKR {s.fee:,.2f}" if s.fee else " · no fee"))
        today = timezone.localdate()
        self.fields["day"].widget.attrs.update({"min": today.isoformat()})
        self.fields["day"].initial = today
        if with_member:
            self.fields["member"].queryset = Member.objects.active().order_by("full_name")
            self.fields["member"].label_from_instance = lambda m: f"{m.full_name} ({m.member_id})"
            self.fields["member"].required = True
        else:
            del self.fields["member"]

    def clean_day(self):
        day = self.cleaned_data["day"]
        if day < timezone.localdate():
            raise forms.ValidationError("Choose today or a future date.")
        return day


def parse_slot(value):
    """Parse a submitted slot (ISO datetime) into an aware datetime, or None."""
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if timezone.is_aware(dt) else timezone.make_aware(dt)


class SessionTypeForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SessionType
        fields = ["name", "duration_minutes", "fee", "description", "is_active"]
        widgets = {"fee": forms.NumberInput(attrs={"min": "0", "step": "0.01"})}
        labels = {"duration_minutes": "Duration", "is_active": "Bookable"}

    def clean_fee(self):
        fee = self.cleaned_data["fee"]
        if fee is None or fee < 0:
            raise forms.ValidationError("The fee can't be negative.")
        return fee


