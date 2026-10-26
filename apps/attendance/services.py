"""
Check-in rules (approved for Phase 3):

1. The member must exist and be active.
2. A membership must cover today and must not be suspended or cancelled.
   Scheduled (future) and expired memberships are refused.
3. The membership's access rules (allowed days and hours, copied from the plan
   when the membership was assigned) must allow entry now, in gym local time.
4. The member must not already be checked in. They may return after
   checking out. A visit left open from an earlier day is closed as
   "no check-out recorded" so it never blocks a new day.
5. An unpaid or partly paid membership does NOT block entry. Staff see a
   balance warning, and the outstanding amount is stored on the visit.
"""
from dataclasses import dataclass, field
from datetime import datetime, time

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.core.utils import log_activity
from apps.members.models import Member
from apps.memberships.models import Membership
from apps.payments.balances import balance_for

from .models import Attendance, CheckInPass, new_pass_token


@dataclass
class CheckInResult:
    ok: bool
    message: str
    member: Member | None = None
    attendance: Attendance | None = None
    warnings: list = field(default_factory=list)


def start_of_today():
    return timezone.make_aware(datetime.combine(timezone.localdate(), time.min))


def close_stale_visits(member=None):
    """Mark visits left open from previous days as having no check-out."""
    qs = Attendance.objects.filter(checked_out_at__isnull=True, auto_closed=False, checked_in_at__lt=start_of_today())
    if member is not None:
        qs = qs.filter(member=member)
    return qs.update(auto_closed=True)


def _covering_membership(member, today):
    """The membership deciding entry today, or a denial message."""
    current = (
        Membership.objects.for_member(member).current(today)
        .order_by("-start_date", "-pk").first()
    )
    if current:
        if current.state == Membership.State.SUSPENDED:
            return None, "This membership is suspended. Entry is not allowed until it is reactivated."
        return current, None
    upcoming = Membership.objects.for_member(member).not_cancelled().filter(start_date__gt=today).order_by("start_date").first()
    if upcoming:
        return None, f"The membership starts on {upcoming.start_date:%d %b %Y}. Entry is not allowed before then."
    last = Membership.objects.for_member(member).not_cancelled().order_by("-end_date").first()
    if last:
        return None, f"The membership expired on {last.end_date:%d %b %Y}. Please renew to enter."
    return None, "This member has no membership. Assign a plan before checking in."


def check_in(member, actor, method=Attendance.Method.MANUAL, request=None):
    try:
        with transaction.atomic():
            member = Member.objects.select_for_update().get(pk=member.pk)
            result = _check_in_locked(member, actor, method)
    except IntegrityError:
        # Lost a race with a simultaneous check-in for the same member.
        return CheckInResult(False, f"{member.full_name} is already checked in.", member=member)
    return result


def _check_in_locked(member, actor, method):
    close_stale_visits(member)
    if not member.is_active:
        return CheckInResult(False, f"{member.full_name}'s member account is inactive.", member=member)
    now = timezone.localtime()
    membership, denial = _covering_membership(member, now.date())
    if denial:
        return CheckInResult(False, denial, member=member)
    access_denial = membership.access_denial_reason(now)
    if access_denial:
        return CheckInResult(False, access_denial, member=member)
    open_visit = Attendance.objects.filter(member=member, checked_out_at__isnull=True, auto_closed=False).first()
    if open_visit:
        return CheckInResult(
            False,
            f"{member.full_name} is already checked in (since {timezone.localtime(open_visit.checked_in_at):%H:%M}).",
            member=member, attendance=open_visit,
        )
    balance = balance_for(membership)
    attendance = Attendance.objects.create(
        member=member, membership=membership, method=method, checked_in_by=actor,
        balance_due_at_checkin=balance.outstanding if balance.outstanding > 0 else None,
    )
    warnings = []
    if balance.outstanding > 0:
        warnings.append(f"Balance outstanding: LKR {balance.outstanding:,.2f} ({balance.status_label.lower()}).")
    days_left = membership.days_remaining
    if days_left <= 7:
        warnings.append(f"Membership ends in {days_left} day{'s' if days_left != 1 else ''} ({membership.end_date:%d %b}).")
    return CheckInResult(True, f"{member.full_name} checked in at {now:%H:%M}.", member=member,
                         attendance=attendance, warnings=warnings)


def parse_qr(raw):
    """Accept '<PREFIX>:<token>' (what the QR holds) or a bare token."""
    from django.conf import settings

    raw = (raw or "").strip()
    prefix = f"{settings.ATTENDANCE_QR_PREFIX}:"
    if raw.upper().startswith(prefix.upper()):
        raw = raw[len(prefix):]
    return raw if 16 <= len(raw) <= 64 and all(c.isalnum() or c in "-_" for c in raw) else None


def check_in_by_qr(raw, actor, request=None):
    token = parse_qr(raw)
    card = CheckInPass.objects.select_related("member").filter(token=token).first() if token else None
    if card is None:
        # Never reveal whether a token was close to valid; log for security review.
        log_activity(ActivityLog.Action.CHECKIN_REJECTED, "Unrecognised QR code presented at check-in",
                     actor=actor, request=request)
        return CheckInResult(False, "QR code not recognised. Check the member in manually or reissue their pass.")
    return check_in(card.member, actor, Attendance.Method.QR, request)


@transaction.atomic
def check_out(attendance, actor):
    attendance = Attendance.objects.select_for_update().select_related("member").get(pk=attendance.pk)
    if not attendance.is_open:
        return CheckInResult(False, "This visit is already closed.", member=attendance.member, attendance=attendance)
    attendance.checked_out_at = timezone.now()
    attendance.checked_out_by = actor
    attendance.save(update_fields=["checked_out_at", "checked_out_by"])
    return CheckInResult(True, f"{attendance.member.full_name} checked out after {attendance.duration_display}.",
                         member=attendance.member, attendance=attendance)


def get_pass(member):
    card, _ = CheckInPass.objects.get_or_create(member=member)
    return card


@transaction.atomic
def reissue_pass(member, actor, request=None):
    card, created = CheckInPass.objects.select_for_update().get_or_create(member=member)
    if not created:
        card.token = new_pass_token()
        card.issued_at = timezone.now()
        card.issued_by = actor
        card.save(update_fields=["token", "issued_at", "issued_by"])
    log_activity(ActivityLog.Action.CHECKIN_PASS_REISSUED,
                 f"Reissued check-in QR pass for {member.member_id} (old code no longer works)",
                 actor=actor, request=request)
    return card
