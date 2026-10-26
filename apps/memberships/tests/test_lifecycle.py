from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.members.models import Member
from apps.memberships import periods, services
from apps.memberships.models import Membership, MembershipEvent, MembershipStatus

from .factories import make_member, make_membership, make_plan

DAY = timedelta(days=1)


class LifecycleBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.staff = make_user(Role.GYM_MANAGER)
        self.member = make_member(joined_on=self.today - timedelta(days=400))
        self.monthly = make_plan(name="Monthly", months=1, price="5000.00")
        self.annual = make_plan(name="Annual", months=12, price="50000.00")
        self.basic = make_plan(name="Basic", months=1, price="3000.00")


class AssignTests(LifecycleBase):
    def test_assign_records_snapshot_and_event(self):
        m = services.assign_membership(self.member, self.monthly, self.today, self.staff, "Promo")
        self.assertEqual(m.start_date, self.today)
        self.assertEqual(m.end_date, periods.period_end(self.today, 1, "month"))
        self.assertEqual((m.plan_name, m.price, m.duration_value), ("Monthly", Decimal("5000.00"), 1))
        self.assertEqual(m.status, MembershipStatus.ACTIVE)
        self.assertEqual(m.kind, Membership.Kind.NEW)
        self.assertEqual(list(m.events.values_list("event_type", flat=True)), ["assigned"])

    def test_future_start_is_scheduled(self):
        m = services.assign_membership(self.member, self.monthly, self.today + 10 * DAY, self.staff)
        self.assertEqual(m.status, MembershipStatus.SCHEDULED)

    def test_overlap_rejected(self):
        services.assign_membership(self.member, self.monthly, self.today, self.staff)
        with self.assertRaisesMessage(services.MembershipError, "overlap"):
            services.assign_membership(self.member, self.annual, self.today + 5 * DAY, self.staff)
        self.assertEqual(Membership.objects.count(), 1)

    def test_back_to_back_periods_allowed(self):
        first = services.assign_membership(self.member, self.monthly, self.today, self.staff)
        second = services.assign_membership(self.member, self.monthly, first.end_date + DAY, self.staff)
        self.assertEqual(second.start_date, first.end_date + DAY)

    def test_invalid_start_dates(self):
        with self.assertRaisesMessage(services.MembershipError, "in the past"):
            services.assign_membership(self.member, self.monthly, self.today - 200 * DAY, self.staff)
        with self.assertRaisesMessage(services.MembershipError, "in the future"):
            services.assign_membership(self.member, self.monthly, self.today + 400 * DAY, self.staff)
        newbie = make_member(joined_on=self.today)
        with self.assertRaisesMessage(services.MembershipError, "registration date"):
            services.assign_membership(newbie, self.monthly, self.today - 3 * DAY, self.staff)

    def test_inactive_member_or_plan_rejected(self):
        inactive = make_member(status=Member.Status.INACTIVE, deactivated_on=self.today)
        with self.assertRaisesMessage(services.MembershipError, "inactive"):
            services.assign_membership(inactive, self.monthly, self.today, self.staff)
        self.monthly.is_active = False
        self.monthly.save()
        with self.assertRaisesMessage(services.MembershipError, "inactive"):
            services.assign_membership(self.member, self.monthly, self.today, self.staff)

    def test_database_rejects_invalid_period(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(
                member=self.member, plan=self.monthly, plan_name="x", duration_value=1, duration_unit="month",
                price=1, start_date=self.today, end_date=self.today - DAY,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_membership(self.member, self.monthly, self.today, state="cancelled")  # no cancelled_on

    def test_rollback_when_a_step_fails(self):
        with mock.patch("apps.memberships.services._event", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                services.assign_membership(self.member, self.monthly, self.today, self.staff)
        self.assertFalse(Membership.objects.exists())
        self.assertFalse(MembershipEvent.objects.exists())


class RenewalTests(LifecycleBase):
    def test_early_renewal_starts_after_current_end(self):
        current = make_membership(self.member, self.monthly, self.today - 10 * DAY)
        renewal = services.renew_membership(self.member, self.monthly, self.staff)
        self.assertEqual(renewal.start_date, current.end_date + DAY)
        self.assertEqual(renewal.end_date, periods.period_end(renewal.start_date, 1, "month"))
        self.assertEqual(renewal.previous, current)
        self.assertEqual(renewal.kind, Membership.Kind.RENEWAL)
        self.assertEqual(renewal.status, MembershipStatus.SCHEDULED)
        # The existing period is untouched.
        current.refresh_from_db()
        self.assertEqual(current.end_date, periods.period_end(self.today - 10 * DAY, 1, "month"))
        self.assertEqual(current.status, MembershipStatus.ACTIVE)
        self.assertIn("Early renewal", renewal.events.get().note)

    def test_renewal_after_expiry_starts_today(self):
        expired = make_membership(self.member, self.monthly, self.today - 60 * DAY)
        self.assertEqual(expired.status, MembershipStatus.EXPIRED)
        renewal = services.renew_membership(self.member, self.monthly, self.staff)
        self.assertEqual(renewal.start_date, self.today)
        self.assertEqual(renewal.status, MembershipStatus.ACTIVE)
        self.assertEqual(Membership.objects.count(), 2)
        self.assertIn("after expiry", renewal.events.get().note)

    def test_only_one_scheduled_renewal(self):
        make_membership(self.member, self.monthly, self.today - 5 * DAY)
        services.renew_membership(self.member, self.monthly, self.staff)
        with self.assertRaisesMessage(services.MembershipError, "already scheduled"):
            services.renew_membership(self.member, self.monthly, self.staff)
        self.assertEqual(Membership.objects.count(), 2)

    def test_renew_without_any_membership(self):
        with self.assertRaisesMessage(services.MembershipError, "no membership to renew"):
            services.renew_membership(self.member, self.monthly, self.staff)

    def test_upgrade_takes_effect_at_next_renewal(self):
        current = make_membership(self.member, self.monthly, self.today - 3 * DAY)
        upgrade = services.renew_membership(self.member, self.annual, self.staff)
        self.assertEqual(upgrade.kind, Membership.Kind.PLAN_CHANGE)
        self.assertEqual(upgrade.start_date, current.end_date + DAY)
        self.assertEqual(upgrade.end_date, periods.period_end(upgrade.start_date, 12, "month"))
        self.assertEqual(upgrade.price, Decimal("50000.00"))
        event = upgrade.events.get()
        self.assertEqual(event.event_type, "plan_changed")
        self.assertTrue(event.note.startswith("Upgrade from Monthly to Annual"))
        current.refresh_from_db()
        self.assertEqual((current.plan_name, current.price), ("Monthly", Decimal("5000.00")))
        self.assertEqual(current.status, MembershipStatus.ACTIVE)

    def test_downgrade(self):
        make_membership(self.member, self.monthly, self.today - 3 * DAY)
        downgrade = services.renew_membership(self.member, self.basic, self.staff)
        self.assertTrue(downgrade.events.get().note.startswith("Downgrade"))

    def test_cancelled_scheduled_renewal_can_be_replaced(self):
        make_membership(self.member, self.monthly, self.today - 3 * DAY)
        scheduled = services.renew_membership(self.member, self.monthly, self.staff)
        services.cancel_membership(scheduled, self.staff, "Wrong plan")
        replacement = services.renew_membership(self.member, self.annual, self.staff)
        self.assertEqual(replacement.start_date, scheduled.start_date)
        self.assertEqual(Membership.objects.count(), 3)  # cancelled period kept in history

    def test_renewal_history_is_preserved(self):
        first = make_membership(self.member, self.monthly, self.today - 70 * DAY)
        second = services.renew_membership(self.member, self.monthly, self.staff)
        third = services.renew_membership(self.member, self.annual, self.staff)
        periods_ = list(self.member.memberships.order_by("start_date"))
        self.assertEqual(periods_, [first, second, third])
        for a, b in zip(periods_, periods_[1:]):
            self.assertLess(a.end_date, b.start_date)


class SuspensionTests(LifecycleBase):
    def test_suspend_and_reactivate_keep_end_date(self):
        m = make_membership(self.member, self.monthly, self.today - 5 * DAY)
        end = m.end_date
        services.suspend_membership(m, self.staff, "Injury")
        m.refresh_from_db()
        self.assertEqual(m.status, MembershipStatus.SUSPENDED)
        self.assertEqual(m.suspended_on, self.today)
        self.assertEqual(m.end_date, end)
        services.reactivate_membership(m, self.staff)
        m.refresh_from_db()
        self.assertEqual(m.status, MembershipStatus.ACTIVE)
        self.assertEqual(m.end_date, end)  # clock kept running: no extension
        self.assertIsNone(m.suspended_on)
        self.assertEqual(list(m.events.order_by("pk").values_list("event_type", flat=True)), ["suspended", "reactivated"])

    def test_suspended_membership_expires_on_schedule(self):
        m = make_membership(self.member, self.monthly, self.today - 40 * DAY,
                            state=Membership.State.SUSPENDED, suspended_on=self.today - 20 * DAY)
        self.assertEqual(m.status, MembershipStatus.EXPIRED)
        with self.assertRaisesMessage(services.MembershipError, "Only suspended, unexpired"):
            services.reactivate_membership(m, self.staff)

    def test_invalid_transitions(self):
        scheduled = make_membership(self.member, self.monthly, self.today + 5 * DAY)
        with self.assertRaisesMessage(services.MembershipError, "Only active"):
            services.suspend_membership(scheduled, self.staff, "x")
        expired = make_membership(make_member(), self.monthly, self.today - 60 * DAY)
        with self.assertRaisesMessage(services.MembershipError, "Only active"):
            services.suspend_membership(expired, self.staff, "x")
        active = make_membership(make_member(), self.monthly, self.today)
        with self.assertRaisesMessage(services.MembershipError, "Only suspended"):
            services.reactivate_membership(active, self.staff)


class CancellationTests(LifecycleBase):
    def test_cancel_is_immediate_and_recorded(self):
        m = make_membership(self.member, self.monthly, self.today - 5 * DAY)
        remaining = m.days_remaining
        services.cancel_membership(m, self.staff, "Relocating")
        m.refresh_from_db()
        self.assertEqual(m.status, MembershipStatus.CANCELLED)
        self.assertEqual(m.cancelled_on, self.today)
        self.assertEqual(m.cancellation_reason, "Relocating")
        self.assertIn(f"{remaining} unused days", m.events.get().note)
        self.assertEqual(m.end_date, periods.period_end(self.today - 5 * DAY, 1, "month"))  # agreement kept

    def test_cancel_suspended_and_scheduled(self):
        suspended = make_membership(self.member, self.monthly, self.today - 2 * DAY,
                                    state=Membership.State.SUSPENDED, suspended_on=self.today)
        services.cancel_membership(suspended, self.staff, "Done")
        suspended.refresh_from_db()
        self.assertIsNone(suspended.suspended_on)
        scheduled = make_membership(make_member(), self.monthly, self.today + 3 * DAY)
        services.cancel_membership(scheduled, self.staff, "Changed mind")
        self.assertIn(f"{scheduled.total_days} unused days", scheduled.events.get().note)

    def test_cannot_cancel_expired_or_cancelled_or_without_reason(self):
        expired = make_membership(self.member, self.monthly, self.today - 60 * DAY)
        with self.assertRaisesMessage(services.MembershipError, "can't be cancelled"):
            services.cancel_membership(expired, self.staff, "x")
        active = make_membership(make_member(), self.monthly, self.today)
        with self.assertRaisesMessage(services.MembershipError, "reason"):
            services.cancel_membership(active, self.staff, "   ")
        services.cancel_membership(active, self.staff, "Done")
        with self.assertRaisesMessage(services.MembershipError, "can't be cancelled"):
            services.cancel_membership(active, self.staff, "Again")

    def test_new_membership_allowed_after_cancellation(self):
        m = make_membership(self.member, self.monthly, self.today - 2 * DAY)
        services.cancel_membership(m, self.staff, "Switching")
        new = services.assign_membership(self.member, self.annual, self.today, self.staff)
        self.assertEqual(new.status, MembershipStatus.ACTIVE)


class QuerySetTests(LifecycleBase):
    def test_expiring_soon_and_lapsed(self):
        soon = make_membership(make_member(), self.monthly, self.today - 27 * DAY)  # ends within a few days
        renewed = make_membership(make_member(), self.monthly, self.today - 27 * DAY)
        services.renew_membership(renewed.member, self.monthly, self.staff)
        later = make_membership(make_member(), self.annual, self.today)
        lapsed = make_membership(make_member(), self.monthly, self.today - 60 * DAY)
        old = make_membership(self.member, self.monthly, self.today - 90 * DAY)
        services.renew_membership(self.member, self.monthly, self.staff)  # old is no longer "lapsed"

        expiring = set(Membership.objects.expiring_soon(days=7))
        self.assertIn(soon, expiring)
        self.assertNotIn(renewed, expiring)  # already renewed
        self.assertNotIn(later, expiring)
        lapsed_set = set(Membership.objects.lapsed())
        self.assertEqual(lapsed_set, {lapsed})
        self.assertNotIn(old, lapsed_set)

    def test_with_status(self):
        active = make_membership(make_member(), self.monthly, self.today)
        scheduled = make_membership(make_member(), self.monthly, self.today + 2 * DAY)
        expired = make_membership(make_member(), self.monthly, self.today - 60 * DAY)
        suspended = make_membership(make_member(), self.monthly, self.today,
                                    state=Membership.State.SUSPENDED, suspended_on=self.today)
        cancelled = make_membership(make_member(), self.monthly, self.today,
                                    state=Membership.State.CANCELLED, cancelled_on=self.today)
        for status, expected in [("active", active), ("scheduled", scheduled), ("expired", expired),
                                 ("suspended", suspended), ("cancelled", cancelled)]:
            self.assertEqual(list(Membership.objects.with_status(status)), [expected], status)
            self.assertEqual(expected.status, status)
