"""
Membership lifecycle rules. Every operation runs in a transaction and locks
the member row first, so concurrent requests for the same member (e.g. two
renewals submitted at once) are serialised and can't create overlapping
periods.

Policies (approved for Phase 2):
- Periods never overlap: a member has at most one non-cancelled membership
  covering any given day.
- Early renewal (current period still running): the new period starts the
  day after the member's latest period ends. Renewal after expiry: the new
  period starts on the renewal date. Only one future (scheduled) period may
  exist at a time.
- Plan changes (upgrade/downgrade) take effect at the next renewal: the
  current period runs to its end and the new plan starts the day after.
- Suspension does not pause the clock: the end date is unchanged and a
  membership that passes its end date while suspended simply expires.
- Cancellation takes effect immediately and requires a reason. Unused days
  are recorded; refunds/credits are handled by the payments module.
- Inactive plans and inactive members can't receive new periods.
- Nothing here records or implies payment. Phase 3 links payments to the
  agreed price stored on each membership.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.core.utils import log_activity
from apps.members.models import Member

from . import periods
from .models import Membership, MembershipEvent, MembershipStatus

Action = ActivityLog.Action


class MembershipError(Exception):
    """A membership operation violates a business rule."""


def _lock_member(member):
    return Member.objects.select_for_update().get(pk=member.pk)


def _lock_membership(membership):
    return Membership.objects.select_for_update().select_related("member").get(pk=membership.pk)


def latest_period(member):
    return Membership.objects.for_member(member).not_cancelled().order_by("-end_date", "-pk").first()


def next_renewal_start(member, today=None):
    """Where a renewal for ``member`` would start under the renewal policy."""
    today = today or timezone.localdate()
    latest = latest_period(member)
    if latest and latest.end_date >= today:
        return latest.end_date + timedelta(days=1)
    return today


def _notify_renewal(membership, changed):
    from apps.core.models import Notification
    from apps.core.notify import notify

    title = "Plan change scheduled" if changed else "Membership renewed"
    notify(member=membership.member, title=title,
           message=f"{membership.plan_name}: {membership.start_date:%d %b %Y} – {membership.end_date:%d %b %Y}. "
                   f"Amount due LKR {membership.price:,.2f}.",
           link="/", category=Notification.Category.MEMBERSHIP, dedupe_key=f"renewal:{membership.pk}", external=True)


def _check_can_receive(member, plan):
    if not member.is_active:
        raise MembershipError("This member is inactive. Reactivate the member first.")
    if not plan.is_active:
        raise MembershipError(f"The plan '{plan.name}' is inactive and can't be assigned.")


def _check_no_overlap(member, start, end):
    clash = (
        Membership.objects.for_member(member).not_cancelled()
        .filter(start_date__lte=end, end_date__gte=start).first()
    )
    if clash:
        raise MembershipError(
            f"These dates overlap the existing membership {clash.plan_name} "
            f"({clash.start_date:%d %b %Y} – {clash.end_date:%d %b %Y})."
        )


def _create_period(member, plan, start, actor, kind, previous=None, notes=""):
    end = periods.period_end(start, plan.duration_value, plan.duration_unit)
    _check_no_overlap(member, start, end)
    return Membership.objects.create(
        member=member, plan=plan, previous=previous, kind=kind,
        plan_name=plan.name, duration_value=plan.duration_value, duration_unit=plan.duration_unit,
        price=plan.price, start_date=start, end_date=end, notes=notes.strip()[:255], created_by=actor,
        access_days=plan.access_days, access_start_time=plan.access_start_time, access_end_time=plan.access_end_time,
    )


def _event(membership, event_type, actor, note=""):
    return MembershipEvent.objects.create(
        membership=membership, event_type=event_type, performed_by=actor, note=note[:255]
    )


@transaction.atomic
def assign_membership(member, plan, start_date, actor, notes="", request=None):
    member = _lock_member(member)
    _check_can_receive(member, plan)
    today = timezone.localdate()
    if start_date < today - timedelta(days=settings.MEMBERSHIP_MAX_BACKDATE_DAYS):
        raise MembershipError(
            f"The start date can't be more than {settings.MEMBERSHIP_MAX_BACKDATE_DAYS} days in the past."
        )
    if start_date > today + timedelta(days=settings.MEMBERSHIP_MAX_ADVANCE_DAYS):
        raise MembershipError(
            f"The start date can't be more than {settings.MEMBERSHIP_MAX_ADVANCE_DAYS} days in the future."
        )
    if start_date < member.joined_on:
        raise MembershipError("A membership can't start before the member's registration date.")
    membership = _create_period(member, plan, start_date, actor, Membership.Kind.NEW, notes=notes)
    _event(membership, MembershipEvent.Type.ASSIGNED, actor, f"{plan.name} at {plan.price} LKR")
    log_activity(Action.MEMBERSHIP_ASSIGNED,
                 f"Assigned {plan.name} to {member.member_id} from {membership.start_date:%d %b %Y}",
                 actor=actor, request=request)
    return membership


@transaction.atomic
def renew_membership(member, plan, actor, notes="", request=None):
    """Append the next period for ``member``. A different plan = plan change."""
    member = _lock_member(member)
    _check_can_receive(member, plan)
    today = timezone.localdate()
    latest = latest_period(member)
    if latest is None:
        raise MembershipError("This member has no membership to renew. Assign a membership instead.")
    if latest.start_date > today:
        raise MembershipError(
            f"A renewal is already scheduled to start on {latest.start_date:%d %b %Y}. "
            "Cancel it first if you need to change it."
        )
    start = next_renewal_start(member, today)
    changed = plan.pk != latest.plan_id
    kind = Membership.Kind.PLAN_CHANGE if changed else Membership.Kind.RENEWAL
    membership = _create_period(member, plan, start, actor, kind, previous=latest, notes=notes)
    _notify_renewal(membership, changed)
    if changed:
        direction = "Upgrade" if plan.price > latest.price else "Downgrade" if plan.price < latest.price else "Change"
        note = f"{direction} from {latest.plan_name} to {plan.name}, effective {start:%d %b %Y}"
        _event(membership, MembershipEvent.Type.PLAN_CHANGED, actor, note)
        log_activity(Action.MEMBERSHIP_PLAN_CHANGED, f"{member.member_id}: {note}", actor=actor, request=request)
    else:
        early = latest.end_date >= today
        note = f"{'Early renewal' if early else 'Renewal after expiry'}, starts {start:%d %b %Y}"
        _event(membership, MembershipEvent.Type.RENEWED, actor, note)
        log_activity(Action.MEMBERSHIP_RENEWED, f"Renewed {plan.name} for {member.member_id} from {start:%d %b %Y}",
                     actor=actor, request=request)
    return membership


@transaction.atomic
def suspend_membership(membership, actor, reason, request=None):
    _lock_member(membership.member)
    membership = _lock_membership(membership)
    if not membership.can_suspend:
        raise MembershipError(f"Only active memberships can be suspended (this one is {membership.status_label.lower()}).")
    membership.state = Membership.State.SUSPENDED
    membership.suspended_on = timezone.localdate()
    membership.save(update_fields=["state", "suspended_on", "updated_at"])
    _event(membership, MembershipEvent.Type.SUSPENDED, actor, reason)
    log_activity(Action.MEMBERSHIP_SUSPENDED, f"Suspended membership of {membership.member.member_id}",
                 actor=actor, request=request)
    return membership


@transaction.atomic
def reactivate_membership(membership, actor, request=None):
    _lock_member(membership.member)
    membership = _lock_membership(membership)
    if not membership.can_reactivate:
        raise MembershipError(
            f"Only suspended, unexpired memberships can be reactivated (this one is {membership.status_label.lower()})."
        )
    days = (timezone.localdate() - membership.suspended_on).days
    membership.state = Membership.State.ACTIVE
    membership.suspended_on = None
    membership.save(update_fields=["state", "suspended_on", "updated_at"])
    _event(membership, MembershipEvent.Type.REACTIVATED, actor,
           f"Suspended for {days} day{'s' if days != 1 else ''}; end date unchanged")
    log_activity(Action.MEMBERSHIP_REACTIVATED, f"Reactivated membership of {membership.member.member_id}",
                 actor=actor, request=request)
    return membership


@transaction.atomic
def cancel_membership(membership, actor, reason, request=None):
    _lock_member(membership.member)
    membership = _lock_membership(membership)
    if not membership.can_cancel:
        raise MembershipError(f"This membership is {membership.status_label.lower()} and can't be cancelled.")
    if not reason.strip():
        raise MembershipError("A cancellation reason is required.")
    unused = membership.days_remaining if membership.status != MembershipStatus.SCHEDULED else membership.total_days
    membership.state = Membership.State.CANCELLED
    membership.suspended_on = None
    membership.cancelled_on = timezone.localdate()
    membership.cancellation_reason = reason.strip()[:255]
    membership.save(update_fields=["state", "suspended_on", "cancelled_on", "cancellation_reason", "updated_at"])
    _event(membership, MembershipEvent.Type.CANCELLED, actor, f"{reason.strip()} ({unused} unused days)")
    log_activity(Action.MEMBERSHIP_CANCELLED, f"Cancelled membership of {membership.member.member_id}",
                 actor=actor, request=request)
    return membership
