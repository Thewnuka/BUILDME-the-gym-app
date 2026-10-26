import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.services import send_account_setup_email
from apps.core.models import ActivityLog
from apps.core.utils import log_activity

from .models import Member

Action = ActivityLog.Action


class MemberStateError(Exception):
    """A requested member status change is not allowed."""


def _unique_username(email):
    base = re.sub(r"[^\w.+-]", "", email.split("@")[0].lower())[:30] or "member"
    User = get_user_model()
    candidate, n = base, 1
    while User.objects.filter(username__iexact=candidate).exists():
        n += 1
        candidate = f"{base}{n}"
    return candidate


@transaction.atomic
def create_portal_login(member, request):
    """
    Create a Member-role login for ``member`` and email a password setup link.
    The link proves the member controls the email address before they can
    see their profile.
    """
    member = Member.objects.select_for_update().get(pk=member.pk)
    if member.user_id:
        raise MemberStateError("This member already has a portal login.")
    if not member.email:
        raise MemberStateError("Add an email address before creating a portal login.")
    User = get_user_model()
    if User.objects.filter(email__iexact=member.email).exists():
        raise MemberStateError("A login with this member's email address already exists.")
    first, _, last = member.full_name.partition(" ")
    user = User(
        username=_unique_username(member.email), email=member.email,
        first_name=first[:150], last_name=last[:150], role=Role.MEMBER,
        created_by=request.user if request.user.is_authenticated else None,
    )
    user.set_unusable_password()
    user._member_profile = member  # tells the post_save signal to link, not create
    user.save()
    member.user = user
    member.save(update_fields=["user", "updated_at"])
    # Sent last so a failure rolls back the account instead of leaving it unusable.
    send_account_setup_email(request, user)
    return user


@transaction.atomic
def register_member(form, actor, request):
    member = form.save(commit=False)
    member.created_by = actor
    member.save()
    log_activity(Action.MEMBER_REGISTERED, f"Registered member {member.member_id}", actor=actor, request=request)
    return member


@transaction.atomic
def deactivate_member(member, actor, reason, request=None):
    """
    Mark a member inactive. All history (memberships, and later payments and
    attendance) is preserved. Open memberships must be cancelled first so
    the membership and payment records stay consistent.
    """
    from apps.memberships.models import Membership

    member = Member.objects.select_for_update().get(pk=member.pk)
    if not member.is_active:
        raise MemberStateError("This member is already inactive.")
    if Membership.objects.for_member(member).open().exists():
        raise MemberStateError(
            "This member has a current or scheduled membership. Cancel it before deactivating the member."
        )
    member.status = Member.Status.INACTIVE
    member.deactivated_on = timezone.localdate()
    member.deactivation_reason = reason.strip()[:255]
    member.save(update_fields=["status", "deactivated_on", "deactivation_reason", "updated_at"])
    if member.user_id:
        get_user_model().objects.filter(pk=member.user_id).update(is_active=False)
    log_activity(Action.MEMBER_DEACTIVATED, f"Deactivated member {member.member_id}", actor=actor, request=request)
    return member


@transaction.atomic
def reactivate_member(member, actor, request=None):
    member = Member.objects.select_for_update().get(pk=member.pk)
    if member.is_active:
        raise MemberStateError("This member is already active.")
    member.status = Member.Status.ACTIVE
    member.deactivated_on = None
    member.deactivation_reason = ""
    member.save(update_fields=["status", "deactivated_on", "deactivation_reason", "updated_at"])
    if member.user_id:
        get_user_model().objects.filter(pk=member.user_id).update(is_active=True)
    log_activity(Action.MEMBER_REACTIVATED, f"Reactivated member {member.member_id}", actor=actor, request=request)
    return member


def ensure_profile_for_user(user):
    """
    Every Member-role login has a member profile. A staff-created login whose
    email matches an unlinked profile is attached to it; otherwise a new
    profile is created. (Public self-registration refuses emails that already
    belong to a member, so it can never claim someone else's profile.)
    """
    if user.role != Role.MEMBER or Member.objects.filter(user=user).exists():
        return None
    profile = None
    if user.email:
        profile = Member.objects.filter(user__isnull=True, email__iexact=user.email).first()
    if profile:
        profile.user = user
        profile.save(update_fields=["user", "updated_at"])
        return profile
    email = user.email or ""
    if email and Member.objects.filter(email__iexact=email).exists():
        email = ""  # never violate the unique-email rule; staff can fix the profile
    return Member.objects.create(
        user=user, full_name=user.get_full_name() or user.username, email=email,
        joined_on=timezone.localdate(), created_by=user.created_by,
    )
