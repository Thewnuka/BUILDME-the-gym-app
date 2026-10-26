from django.contrib.auth.models import AbstractUser, Group, UserManager as DjangoUserManager
from django.core.validators import RegexValidator
from django.db import models
from django.urls import reverse

from .roles import MODULE_PERMISSIONS, ROLE_BADGES, Role, group_name

phone_validator = RegexValidator(
    regex=r"^\+?[0-9\s\-()]{7,20}$",
    message="Enter a valid phone number (digits, spaces, dashes, optional leading +).",
)


class UserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.SUPER_ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER, db_index=True)
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    created_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="created_users"
    )
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    REQUIRED_FIELDS = ["email"]

    class Meta:
        ordering = ["first_name", "last_name", "username"]
        permissions = [(codename, name) for codename, name, _module in MODULE_PERMISSIONS]

    def __str__(self):
        return self.get_full_name() or self.username

    def get_absolute_url(self):
        return reverse("accounts:user_detail", args=[self.pk])

    @property
    def is_super_admin(self):
        return self.is_active and self.role == Role.SUPER_ADMIN and self.is_superuser

    @property
    def is_member(self):
        return self.role == Role.MEMBER

    @property
    def role_badge(self):
        return ROLE_BADGES.get(self.role, "secondary")

    @property
    def initials(self):
        parts = [self.first_name[:1], self.last_name[:1]]
        return ("".join(parts) or self.username[:2]).upper()

    def save(self, *args, **kwargs):
        # The role is the single source of truth for elevated flags: only the
        # Super Admin is a Django superuser / may use the Django admin site.
        is_super = self.role == Role.SUPER_ADMIN
        self.is_superuser = is_super
        self.is_staff = is_super
        if self.email:
            self.email = self.email.strip().lower()
        role_changed = True
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values_list("role", flat=True).first()
            role_changed = previous != self.role
        super().save(*args, **kwargs)
        if role_changed:
            self.sync_role_group()

    def sync_role_group(self):
        group, _ = Group.objects.get_or_create(name=group_name(self.role))
        self.groups.set([group])
        # Drop any cached permissions on this instance.
        for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            self.__dict__.pop(attr, None)
