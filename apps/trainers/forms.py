from django import forms
from django.conf import settings

from apps.accounts.forms import _UserFieldsMixin
from apps.accounts.models import User
from apps.core.forms import BootstrapFormMixin
from apps.core.uploads import validate_upload
from apps.members.forms import DateInput
from apps.members.models import Member
from apps.memberships.forms import TimeInput

from .models import Availability, CompensationRecord, TimeOff, TrainerCertificate, TrainerProfile


class TrainerCreateForm(BootstrapFormMixin, _UserFieldsMixin, forms.ModelForm):
    """Creates the Trainer login; the trainer sets their own password via an emailed link."""

    phone = forms.CharField(max_length=20, required=False)
    specialties = forms.CharField(max_length=255, required=False,
                                  help_text="Comma-separated, e.g. 'Strength, Weight loss'.")
    employment_type = forms.ChoiceField(choices=TrainerProfile.EmploymentType.choices)
    hired_on = forms.DateField(required=False, widget=DateInput())

    class Meta:
        model = User
        fields = ["first_name", "last_name", "username", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True


class TrainerProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = TrainerProfile
        fields = ["phone", "specialties", "bio", "qualifications", "photo",
                  "employment_status", "employment_type", "hired_on"]
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 3}),
            "qualifications": forms.Textarea(attrs={"rows": 3}),
            "hired_on": DateInput(),
            "photo": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        }

    def __init__(self, *args, staff_edit=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not staff_edit:
            # Trainers editing their own profile can't change employment details.
            for name in ("employment_status", "employment_type", "hired_on"):
                del self.fields[name]

    def clean_photo(self):
        return validate_upload(self.cleaned_data.get("photo"), settings.MEMBER_PHOTO_MAX_BYTES)


class AvailabilityForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Availability
        fields = ["weekday", "start_time", "end_time"]
        widgets = {"start_time": TimeInput(), "end_time": TimeInput()}

    def __init__(self, *args, trainer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.trainer = trainer

    def clean(self):
        cleaned = super().clean()
        start, end, day = cleaned.get("start_time"), cleaned.get("end_time"), cleaned.get("weekday")
        if start and end and start >= end:
            self.add_error("end_time", "The end time must be after the start time.")
        elif start and end and day is not None and self.trainer:
            clash = Availability.objects.filter(trainer=self.trainer, weekday=day,
                                                start_time__lt=end, end_time__gt=start).first()
            if clash:
                self.add_error(None, f"This overlaps the existing window {clash}.")
        return cleaned


class TimeOffForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = TimeOff
        fields = ["start_date", "end_date", "reason"]
        widgets = {"start_date": DateInput(), "end_date": DateInput()}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("start_date") and cleaned.get("end_date") and cleaned["end_date"] < cleaned["start_date"]:
            self.add_error("end_date", "The end date must be on or after the start date.")
        return cleaned


class CertificateForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = TrainerCertificate
        fields = ["title", "issuer", "issued_on", "expires_on", "file"]
        widgets = {"issued_on": DateInput(), "expires_on": DateInput(),
                   "file": forms.ClearableFileInput(attrs={"accept": "application/pdf,image/jpeg,image/png,image/webp"})}
        help_texts = {"file": "PDF, JPEG, PNG or WebP."}

    def clean_file(self):
        return validate_upload(self.cleaned_data.get("file"), settings.TRAINER_FILE_MAX_BYTES, allow_pdf=True)

    def clean(self):
        cleaned = super().clean()
        issued, expires = cleaned.get("issued_on"), cleaned.get("expires_on")
        if issued and expires and expires < issued:
            self.add_error("expires_on", "The expiry date must be after the issue date.")
        return cleaned


class AssignMemberForm(BootstrapFormMixin, forms.Form):
    member = forms.ModelChoiceField(queryset=Member.objects.none(), empty_label="Choose a member…")
    start_date = forms.DateField(widget=DateInput())
    goal = forms.CharField(max_length=255, required=False,
                           widget=forms.TextInput(attrs={"placeholder": "e.g. Lose 5 kg in 3 months"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.utils import timezone

        self.fields["member"].queryset = Member.objects.active().order_by("full_name")
        self.fields["member"].label_from_instance = lambda m: f"{m.full_name} ({m.member_id})"
        self.fields["start_date"].initial = timezone.localdate()


class AssignTrainerForm(BootstrapFormMixin, forms.Form):
    trainer = forms.ModelChoiceField(queryset=TrainerProfile.objects.none(), empty_label="Choose a trainer…")
    goal = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["trainer"].queryset = TrainerProfile.objects.active().select_related("user")


class PayRecordForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CompensationRecord
        fields = ["period", "kind", "amount", "notes"]
        widgets = {"period": DateInput(), "amount": forms.NumberInput(attrs={"min": "0.01", "step": "0.01"})}

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount is None or amount <= 0:
            raise forms.ValidationError("The amount must be greater than zero.")
        return amount
