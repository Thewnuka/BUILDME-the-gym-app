import uuid
from pathlib import Path

from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import phone_validator


def normalize_phone(value):
    """
    Canonical phone form used for storage and duplicate detection:
    formatting removed, local numbers (leading 0) given the default country code.
    "077 123-4567" -> "+94771234567"
    """
    if not value:
        return ""
    raw = value.strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if raw.startswith("+"):
        return f"+{digits}"
    if raw.startswith("00"):
        return f"+{digits[2:]}"
    code = settings.PHONE_DEFAULT_COUNTRY_CODE
    if code and digits.startswith("0"):
        return f"+{code}{digits[1:]}"
    return digits


def member_photo_path(instance, filename):
    # Random file names: the original name is never exposed or trusted.
    ext = Path(filename).suffix.lower()
    return f"members/photos/{uuid.uuid4().hex}{ext}"


class MemberQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=Member.Status.ACTIVE)

    def search(self, term):
        term = (term or "").strip()
        if not term:
            return self
        query = Q(full_name__icontains=term) | Q(member_id__iexact=term) | Q(email__icontains=term)
        phone = normalize_phone(term)
        if len(phone.lstrip("+")) >= 3:
            query |= Q(phone__contains=phone.lstrip("+"))
        return self.filter(query)


class Member(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    member_id = models.CharField("member ID", max_length=20, unique=True, null=True, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="member_profile",
        help_text="Portal login linked to this member, if any.",
    )
    full_name = models.CharField(max_length=150, db_index=True)
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    joined_on = models.DateField("registration date", default=timezone.localdate, db_index=True)
    photo = models.ImageField(upload_to=member_photo_path, blank=True)
    fitness_goals = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    deactivated_on = models.DateField(null=True, blank=True)
    deactivation_reason = models.CharField(max_length=255, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MemberQuerySet.as_manager()

    class Meta:
        ordering = ["-joined_on", "-pk"]
        constraints = [
            models.UniqueConstraint(Lower("email"), condition=~Q(email=""), name="member_unique_email_ci"),
            models.UniqueConstraint(fields=["phone"], condition=~Q(phone=""), name="member_unique_phone"),
            models.CheckConstraint(
                condition=Q(date_of_birth__isnull=True) | Q(date_of_birth__lte=models.F("joined_on")),
                name="member_dob_before_registration",
            ),
            models.CheckConstraint(
                condition=Q(status="active", deactivated_on__isnull=True)
                | Q(status="inactive", deactivated_on__isnull=False),
                name="member_status_matches_deactivation",
            ),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.member_id})" if self.member_id else self.full_name

    def get_absolute_url(self):
        return reverse("members:detail", args=[self.pk])

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    @property
    def initials(self):
        parts = [p[:1] for p in self.full_name.split()[:2]]
        return ("".join(parts) or "?").upper()

    @property
    def age(self):
        if not self.date_of_birth:
            return None
        today = timezone.localdate()
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @property
    def status_badge(self):
        return "success" if self.is_active else "secondary"

    def save(self, *args, **kwargs):
        self.phone = normalize_phone(self.phone)
        self.emergency_contact_phone = normalize_phone(self.emergency_contact_phone)
        self.email = (self.email or "").strip().lower()
        if self.member_id:
            return super().save(*args, **kwargs)
        # The ID is derived from the primary key, so it can never collide,
        # even with concurrent registrations.
        with transaction.atomic():
            super().save(*args, **kwargs)
            self.member_id = f"{settings.MEMBER_ID_PREFIX}-{self.pk:06d}"
            type(self).objects.filter(pk=self.pk).update(member_id=self.member_id)
