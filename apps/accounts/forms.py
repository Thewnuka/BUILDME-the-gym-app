from django import forms
from django.apps import apps
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.core.validators import RegexValidator
from django.core.cache import cache

from apps.core.forms import BootstrapFormMixin
from apps.core.utils import get_client_ip

from .models import User
from .roles import Role, assignable_roles


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    username = forms.CharField(
        label="Username or email",
        max_length=254,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username", "placeholder": "you@example.com"}),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "placeholder": "••••••••"}),
    )
    remember_me = forms.BooleanField(label="Keep me signed in", required=False)

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Invalid credentials or inactive account. Please try again.",
        "locked": "Too many failed sign-in attempts. Please wait a few minutes and try again.",
    }

    def _throttle_key(self):
        username = (self.data.get("username") or "").strip().lower()
        return f"login-fail:{get_client_ip(self.request)}:{username}"

    def clean(self):
        key = self._throttle_key()
        if cache.get(key, 0) >= settings.LOGIN_MAX_ATTEMPTS:
            raise forms.ValidationError(self.error_messages["locked"], code="locked")
        try:
            cleaned = super().clean()
        except forms.ValidationError:
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, settings.LOGIN_LOCKOUT_SECONDS)
            else:
                cache.touch(key, settings.LOGIN_LOCKOUT_SECONDS)
            raise
        cache.delete(key)
        return cleaned


class StyledPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    pass


class StyledPasswordResetForm(BootstrapFormMixin, PasswordResetForm):
    pass


class StyledSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    pass


class _UserFieldsMixin:
    """Shared validation for user-editing forms."""

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        qs = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A user with this email address already exists.")
        # Sign-in accepts a username or an email, so an email must never
        # collide with someone else's username.
        clash = User.objects.filter(username__iexact=email)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("This email address can't be used. Please choose another.")
        return email

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        qs = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("This username is already taken.")
        return username


class _RoleRestrictedMixin:
    """Limit role choices to roles the acting user may assign (server-side)."""

    def restrict_roles(self, actor):
        allowed = assignable_roles(actor)
        self.fields["role"].choices = [(r.value, r.label) for r in allowed]
        self._allowed_roles = {r.value for r in allowed}

    def clean_role(self):
        role = self.cleaned_data["role"]
        if role not in self._allowed_roles:
            raise forms.ValidationError("You are not allowed to assign this role.")
        return role


class UserCreateForm(BootstrapFormMixin, _UserFieldsMixin, _RoleRestrictedMixin, forms.ModelForm):
    SET_PASSWORD = "set"
    EMAIL_INVITE = "email"

    password_method = forms.ChoiceField(
        label="Password setup",
        choices=[
            (SET_PASSWORD, "Set a password now"),
            (EMAIL_INVITE, "Email the user a link to set their own password"),
        ],
        initial=SET_PASSWORD,
        widget=forms.RadioSelect,
    )
    password1 = forms.CharField(
        label="Password", required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=password_validation.password_validators_help_text_html(),
    )
    password2 = forms.CharField(
        label="Confirm password", required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "username", "email", "phone", "role", "is_active"]
        labels = {"is_active": "Account is active"}

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
        self.fields["is_active"].initial = True
        self.restrict_roles(actor)
        if not self.is_bound and Role.MEMBER.value in self._allowed_roles:
            self.initial.setdefault("role", Role.MEMBER.value)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password_method") == self.SET_PASSWORD:
            p1, p2 = cleaned.get("password1"), cleaned.get("password2")
            if not p1:
                self.add_error("password1", "Enter a password.")
            elif p1 != p2:
                self.add_error("password2", "The two password fields didn't match.")
            else:
                user = User(
                    username=cleaned.get("username", ""),
                    email=cleaned.get("email", ""),
                    first_name=cleaned.get("first_name", ""),
                    last_name=cleaned.get("last_name", ""),
                )
                try:
                    password_validation.validate_password(p1, user)
                except forms.ValidationError as exc:
                    self.add_error("password1", exc)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.created_by = self.actor
        if self.cleaned_data["password_method"] == self.SET_PASSWORD:
            user.set_password(self.cleaned_data["password1"])
        else:
            user.set_unusable_password()
        if commit:
            user.save()
        return user


class UserUpdateForm(BootstrapFormMixin, _UserFieldsMixin, _RoleRestrictedMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "username", "email", "phone", "role"]

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
        self.restrict_roles(actor)
        if self.instance.pk == actor.pk:
            # Nobody can change their own role (prevents self-escalation and
            # the last Super Admin locking everyone out).
            self.fields["role"].choices = [(self.instance.role, self.instance.get_role_display())]
            self._allowed_roles = {self.instance.role}
            self.fields["role"].disabled = True
            self.fields["role"].help_text = "You cannot change your own role."


class AdminSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    pass


class ProfileForm(BootstrapFormMixin, _UserFieldsMixin, forms.ModelForm):
    """Self-service profile editing. Role and status are never editable here."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True


full_name_validator = RegexValidator(
    regex=r"^[^\W\d_]+(?:\.?[ '\-]+[^\W\d_]+)*\.?$",
    message="Enter your full name using letters, spaces, apostrophes, periods or hyphens.",
)


class RegistrationForm(BootstrapFormMixin, _UserFieldsMixin, UserCreationForm):
    """
    Public self-registration. Builds on Django's UserCreationForm, so the
    password is validated by AUTH_PASSWORD_VALIDATORS and stored hashed.
    New accounts always get the Member role, whatever the request contains.
    """

    full_name = forms.CharField(
        label="Full name",
        max_length=150,
        validators=[full_name_validator],
        widget=forms.TextInput(attrs={"autocomplete": "name", "autofocus": True, "placeholder": "Jane Doe"}),
    )
    email = forms.EmailField(
        label="Email address",
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "you@example.com"}),
    )

    field_order = ["full_name", "username", "email", "password1", "password2"]

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ["username", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({"autofocus": False, "placeholder": "janedoe"})
        self.fields["username"].help_text = "Up to 150 characters: letters, digits and . + - _ only."
        self.fields["password1"].label = "Password"
        self.fields["password2"].label = "Confirm password"
        self.fields["password2"].help_text = "Enter the same password again."

    def clean_full_name(self):
        return " ".join(self.cleaned_data["full_name"].split())

    def clean_email(self):
        email = super().clean_email()
        # An email already on a member profile was entered by gym staff. It
        # can only be linked to a login via the emailed setup link, which
        # proves the person owns the address.
        Member = apps.get_model("members", "Member")
        if Member.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "This email address is already registered with the gym. "
                "Please ask the front desk to send you a sign-in link."
            )
        return email

    def clean_username(self):
        username = super().clean_username()
        if "@" in username:
            raise forms.ValidationError("Usernames can't contain '@'. Use letters, digits and . + - _ only.")
        if User.objects.filter(email__iexact=username).exists():
            raise forms.ValidationError("This username is already taken.")
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        first, _, last = self.cleaned_data["full_name"].partition(" ")
        user.first_name, user.last_name = first[:150], last[:150]
        user.role = Role.MEMBER
        user.is_active = True
        if commit:
            user.save()
        return user
