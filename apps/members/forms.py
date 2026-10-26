from datetime import date

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.core.forms import BootstrapFormMixin
from apps.core.uploads import validate_upload

from .models import Member, normalize_phone

MAX_AGE_YEARS = 120


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class MemberForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Member
        fields = [
            "full_name", "phone", "email", "date_of_birth", "address",
            "emergency_contact_name", "emergency_contact_phone",
            "joined_on", "photo", "fitness_goals",
        ]
        labels = {
            "phone": "Phone number",
            "email": "Email address",
            "emergency_contact_name": "Emergency contact name",
            "emergency_contact_phone": "Emergency contact phone",
        }
        widgets = {
            "date_of_birth": DateInput(),
            "joined_on": DateInput(),
            "address": forms.Textarea(attrs={"rows": 2}),
            "fitness_goals": forms.Textarea(attrs={"rows": 3, "placeholder": "e.g. Lose 5 kg, build strength, train for a 10K"}),
            "photo": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        }
        help_texts = {
            "phone": "Local numbers are saved with the +{} country code.",
            "photo": "JPEG, PNG or WebP, up to {} KB.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].required = True
        self.fields["phone"].help_text = self.fields["phone"].help_text.format(settings.PHONE_DEFAULT_COUNTRY_CODE)
        self.fields["photo"].help_text = self.fields["photo"].help_text.format(settings.MEMBER_PHOTO_MAX_BYTES // 1024)
        today = timezone.localdate().isoformat()
        self.fields["date_of_birth"].widget.attrs["max"] = today
        self.fields["joined_on"].widget.attrs["max"] = today

    def clean_full_name(self):
        name = " ".join(self.cleaned_data["full_name"].split())
        if len(name) < 2:
            raise forms.ValidationError("Enter the member's full name.")
        return name

    def _others(self):
        qs = Member.objects.all()
        return qs.exclude(pk=self.instance.pk) if self.instance.pk else qs

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        if len(phone.lstrip("+")) < 7:
            raise forms.ValidationError("Enter a valid phone number.")
        existing = self._others().filter(phone=phone).first()
        if existing:
            raise forms.ValidationError(f"This phone number is already registered to member {existing.member_id}.")
        return phone

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if email:
            existing = self._others().filter(email__iexact=email).first()
            if existing:
                raise forms.ValidationError(f"This email address is already registered to member {existing.member_id}.")
        return email

    def clean_emergency_contact_phone(self):
        return normalize_phone(self.cleaned_data.get("emergency_contact_phone"))

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if dob:
            today = timezone.localdate()
            if dob > today:
                raise forms.ValidationError("Date of birth can't be in the future.")
            if dob < date(today.year - MAX_AGE_YEARS, today.month, 1):
                raise forms.ValidationError("Please check the date of birth.")
        return dob

    def clean_joined_on(self):
        joined = self.cleaned_data.get("joined_on")
        if joined and joined > timezone.localdate():
            raise forms.ValidationError("Registration date can't be in the future.")
        return joined

    def clean_photo(self):
        return validate_upload(self.cleaned_data.get("photo"), settings.MEMBER_PHOTO_MAX_BYTES)

    def clean(self):
        cleaned = super().clean()
        name, phone = cleaned.get("emergency_contact_name"), cleaned.get("emergency_contact_phone")
        if name and not phone:
            self.add_error("emergency_contact_phone", "Enter the emergency contact's phone number.")
        if phone and not name:
            self.add_error("emergency_contact_name", "Enter the emergency contact's name.")
        if phone and phone == cleaned.get("phone"):
            self.add_error("emergency_contact_phone", "The emergency contact must be a different number.")
        dob, joined = cleaned.get("date_of_birth"), cleaned.get("joined_on")
        if dob and joined and dob > joined:
            self.add_error("date_of_birth", "Date of birth must be before the registration date.")
        return cleaned


class MemberCreateForm(MemberForm):
    create_login = forms.BooleanField(
        label="Create a member portal login",
        required=False,
        help_text="Emails the member a link to set their password. Requires an email address.",
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("create_login"):
            email = cleaned.get("email")
            if not email:
                self.add_error("email", "An email address is required to create a portal login.")
            elif get_user_model().objects.filter(email__iexact=email).exists():
                self.add_error("email", "A login with this email address already exists.")
        return cleaned


class MemberDeactivateForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(
        label="Reason", max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Moved away, requested account closure"}),
    )
