"""
Recurring jobs. Run them all with ``python manage.py run_scheduled_tasks``
(cron / Windows Task Scheduler every 15–60 minutes), or via Celery beat if
configured (config/celery.py). Every reminder uses a dedupe key, so running
the jobs more often (or twice at once) never duplicates a reminder.

Reminder rules
* Membership expiring: one reminder per value in MEMBERSHIP_EXPIRY_REMINDER_DAYS
  (default 7 and 1 days), sent once the end date is that close and the
  membership hasn't been renewed.
* Membership expired: once, within 7 days after a not-renewed membership ended.
* Payment due: once per membership with an unpaid balance, from
  PAYMENT_DUE_REMINDER_DAYS before its start date (or as soon as it has
  started unpaid). A membership's payment is due on its start date.
* Training sessions: see apps.appointments.services.send_reminders.
* Equipment maintenance and low stock: staff notifications (in-app).
* Announcements: scheduled ones are published when their time comes.
"""
import logging
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from . import gym
from .models import Notification
from .notify import notify

logger = logging.getLogger(__name__)
C = Notification.Category


def _member_link(membership):
    return reverse("dashboard:home")


def membership_expiry_reminders():
    from apps.memberships.models import Membership

    sent = 0
    for days in gym.membership_expiry_reminder_days():
        for m in Membership.objects.expiring_soon(days).filter(member__status="active").select_related("member__user"):
            left = (m.end_date - timezone.localdate()).days
            when = "today" if left <= 0 else ("tomorrow" if left == 1 else f"in {left} days")
            if notify(member=m.member, title=f"Your membership ends {when}",
                      message=f"{m.plan_name} ends on {m.end_date:%d %b %Y}. Renew at the front desk to keep training.",
                      link=_member_link(m), category=C.MEMBERSHIP, dedupe_key=f"membership_expiring:{m.pk}:{days}",
                      external=True):
                sent += 1
    return sent


def expired_membership_notices():
    from apps.memberships.models import Membership

    today = timezone.localdate()
    sent = 0
    qs = Membership.objects.lapsed().filter(end_date__gte=today - timedelta(days=7), member__status="active")
    for m in qs.select_related("member__user"):
        if notify(member=m.member, title="Your membership has expired",
                  message=f"{m.plan_name} ended on {m.end_date:%d %b %Y}. Visit the front desk to renew.",
                  link=_member_link(m), category=C.MEMBERSHIP, dedupe_key=f"membership_expired:{m.pk}", external=True):
            sent += 1
    return sent


def payment_due_reminders():
    from apps.memberships.models import Membership
    from apps.payments.balances import with_balances

    today = timezone.localdate()
    horizon = today + timedelta(days=gym.payment_due_reminder_days())
    qs = with_balances(Membership.objects.open().filter(start_date__lte=horizon, member__status="active")) \
        .filter(outstanding__gt=0).select_related("member__user")
    sent = 0
    for m in qs:
        due = "now" if m.start_date <= today else f"on {m.start_date:%d %b %Y}"
        if notify(member=m.member, title="Membership payment due",
                  message=f"LKR {m.outstanding:,.2f} is due {due} for {m.plan_name}.", link=_member_link(m),
                  category=C.PAYMENT, dedupe_key=f"payment_due:{m.pk}", external=True):
            sent += 1
    return sent


def run_all():
    """Run every recurring job; one failing job never stops the others."""
    from apps.announcements.services import publish_due
    from apps.appointments.services import send_reminders
    from apps.attendance.services import close_stale_visits
    from apps.equipment.services import send_maintenance_reminders
    from apps.inventory.services import sweep_low_stock

    jobs = {
        "membership_expiring": membership_expiry_reminders,
        "membership_expired": expired_membership_notices,
        "payment_due": payment_due_reminders,
        "appointment_reminders": send_reminders,
        "equipment_maintenance": send_maintenance_reminders,
        "low_stock": sweep_low_stock,
        "announcements": publish_due,
        "stale_visits_closed": close_stale_visits,
    }
    results = {}
    for name, job in jobs.items():
        try:
            results[name] = job()
        except Exception:
            logger.exception("Scheduled job %s failed", name)
            results[name] = "error"
    return results

