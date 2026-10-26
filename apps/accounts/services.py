from django.contrib.auth.models import Group, Permission
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core import gym

from .roles import DEFAULT_ROLE_PERMISSIONS, MODULE_PERMISSIONS, RESERVED_PERMISSIONS, Role, group_name


def module_permissions():
    """Permission objects for the module catalogue, keyed by codename."""
    codenames = [p[0] for p in MODULE_PERMISSIONS]
    perms = Permission.objects.filter(content_type__app_label="accounts", codename__in=codenames)
    return {p.codename: p for p in perms}


@transaction.atomic
def ensure_role_groups(reset=False):
    """
    Create one group per role. Defaults are applied to newly created groups,
    or to all groups when ``reset`` is true. Existing customisations made by
    the Super Admin are preserved otherwise.
    """
    perms = module_permissions()
    if not perms:
        return
    for role in Role:
        group, created = Group.objects.get_or_create(name=group_name(role))
        if created or reset:
            group.permissions.set([perms[c] for c in DEFAULT_ROLE_PERMISSIONS[role] if c in perms])
        elif role == Role.SUPER_ADMIN:
            # Super Admin always holds every module permission.
            group.permissions.add(*perms.values())
        elif role == Role.MEMBER:
            # Members use their own self-service portal, which needs no module
            # permissions. Staff-area permissions would expose other members' data.
            group.permissions.clear()


@transaction.atomic
def set_role_permissions(role, codenames):
    """Replace a role's permissions; reserved permissions cannot be granted."""
    role = Role(role)
    if role == Role.SUPER_ADMIN:
        raise ValueError("Super Admin permissions cannot be changed.")
    if role == Role.MEMBER:
        raise ValueError("The Member role is portal-only and can't be given staff permissions.")
    perms = module_permissions()
    allowed = [perms[c] for c in codenames if c in perms and c not in RESERVED_PERMISSIONS]
    group, _ = Group.objects.get_or_create(name=group_name(role))
    group.permissions.set(allowed)
    return group


def role_permission_codenames(role):
    group = Group.objects.filter(name=group_name(role)).first()
    if not group:
        return set()
    return set(group.permissions.filter(content_type__app_label="accounts").values_list("codename", flat=True))


def send_account_setup_email(request, user):
    """Email a one-time link that lets a new user set their own password."""
    context = {
        "user": user,
        "protocol": "https" if request.is_secure() else "http",
        "domain": request.get_host(),
        "uid": urlsafe_base64_encode(force_bytes(user.pk)),
        "token": default_token_generator.make_token(user),
        "brand": gym.gym_name(),
    }
    subject = render_to_string("registration/account_setup_subject.txt", context).strip()
    body = render_to_string("registration/account_setup_email.txt", context)
    send_mail(subject, body, None, [user.email])
