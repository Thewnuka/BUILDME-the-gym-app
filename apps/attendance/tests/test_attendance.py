from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.attendance import services
from apps.attendance.models import Attendance, CheckInPass
from apps.core.models import ActivityLog
from apps.members.models import Member
from apps.memberships import services as membership_services
from apps.memberships.models import Membership
from apps.memberships.tests.factories import make_member, make_membership, make_plan
from apps.payments import services as payment_services

DAY = timedelta(days=1)


class AttendanceBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.desk = make_user(Role.RECEPTIONIST)
        self.manager = make_user(Role.GYM_MANAGER)
        self.plan = make_plan(name="Monthly", price="6000")
        self.member = make_member(full_name="Amaya Silva", phone="0771234567", joined_on=self.today - 90 * DAY)
        self.membership = make_membership(self.member, self.plan, self.today - 3 * DAY)

    def at(self, hour, minute=0, day=None):
        """Patch 'now' for access-rule checks to a local time today (or ``day``)."""
        moment = timezone.make_aware(datetime.combine(day or self.today, time(hour, minute)))
        return mock.patch("apps.attendance.services.timezone.localtime", return_value=moment)


class CheckInRuleTests(AttendanceBase):
    def test_successful_check_in_with_balance_warning(self):
        result = services.check_in(self.member, self.desk)
        self.assertTrue(result.ok, result.message)
        visit = result.attendance
        self.assertEqual((visit.member, visit.membership, visit.checked_in_by), (self.member, self.membership, self.desk))
        self.assertTrue(visit.is_open)
        self.assertEqual(visit.balance_due_at_checkin, Decimal("6000.00"))
        self.assertIn("Balance outstanding: LKR 6,000.00", result.warnings[0])

    def test_no_warning_when_paid(self):
        payment_services.record_payment(self.membership, Decimal("6000"), "cash", self.today, self.desk)
        result = services.check_in(self.member, self.desk)
        self.assertTrue(result.ok)
        self.assertIsNone(result.attendance.balance_due_at_checkin)
        self.assertFalse(any("Balance" in w for w in result.warnings))

    def test_denials(self):
        cases = []
        inactive = make_member(status=Member.Status.INACTIVE, deactivated_on=self.today)
        cases.append((inactive, "inactive"))
        cases.append((make_member(), "no membership"))
        expired = make_member(joined_on=self.today - 200 * DAY)
        make_membership(expired, self.plan, self.today - 80 * DAY)
        cases.append((expired, "expired on"))
        scheduled = make_member()
        make_membership(scheduled, self.plan, self.today + 5 * DAY)
        cases.append((scheduled, "starts on"))
        suspended = make_member()
        make_membership(suspended, self.plan, self.today, state=Membership.State.SUSPENDED, suspended_on=self.today)
        cases.append((suspended, "suspended"))
        cancelled = make_member()
        m = make_membership(cancelled, self.plan, self.today)
        membership_services.cancel_membership(m, self.manager, "Left")
        cases.append((cancelled, "no membership"))
        for member, expected in cases:
            result = services.check_in(member, self.desk)
            self.assertFalse(result.ok, expected)
            self.assertIn(expected, result.message, expected)
        self.assertFalse(Attendance.objects.exists())

    def test_access_hours_and_days(self):
        Membership.objects.filter(pk=self.membership.pk).update(
            access_start_time=time(6, 0), access_end_time=time(16, 0),
            access_days=",".join(str(d) for d in range(7) if d != self.today.weekday()),
        )
        with self.at(10):
            result = services.check_in(self.member, self.desk)
        self.assertFalse(result.ok)
        self.assertIn("only on", result.message)
        Membership.objects.filter(pk=self.membership.pk).update(access_days="")
        with self.at(17, 30):
            result = services.check_in(self.member, self.desk)
        self.assertFalse(result.ok)
        self.assertIn("between 06:00 and 16:00", result.message)
        with self.at(9, 15):
            result = services.check_in(self.member, self.desk)
        self.assertTrue(result.ok, result.message)

    def test_rules_come_from_membership_snapshot_not_current_plan(self):
        self.plan.access_start_time, self.plan.access_end_time = time(6, 0), time(7, 0)
        self.plan.save()  # tightening the plan later doesn't change existing agreements
        with self.at(20):
            self.assertTrue(services.check_in(self.member, self.desk).ok)

    def test_duplicate_check_in_prevented_and_reentry_allowed(self):
        first = services.check_in(self.member, self.desk)
        second = services.check_in(self.member, self.desk)
        self.assertFalse(second.ok)
        self.assertIn("already checked in", second.message)
        self.assertEqual(Attendance.objects.count(), 1)
        services.check_out(first.attendance, self.desk)
        third = services.check_in(self.member, self.desk)
        self.assertTrue(third.ok)
        self.assertEqual(Attendance.objects.count(), 2)

    def test_database_blocks_two_open_visits(self):
        services.check_in(self.member, self.desk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attendance.objects.create(member=self.member, membership=self.membership)

    def test_concurrent_duplicate_returns_clean_message(self):
        with mock.patch("apps.attendance.services._check_in_locked", side_effect=IntegrityError):
            result = services.check_in(self.member, self.desk)
        self.assertFalse(result.ok)
        self.assertIn("already checked in", result.message)

    def test_stale_open_visit_from_yesterday_is_closed(self):
        yesterday = timezone.now() - DAY
        stale = Attendance.objects.create(member=self.member, membership=self.membership, checked_in_at=yesterday)
        result = services.check_in(self.member, self.desk)
        self.assertTrue(result.ok, result.message)
        stale.refresh_from_db()
        self.assertTrue(stale.auto_closed)
        self.assertIsNone(stale.checked_out_at)
        self.assertEqual(stale.status_label, "No check-out")

    def test_check_out(self):
        visit = services.check_in(self.member, self.desk).attendance
        result = services.check_out(visit, self.desk)
        self.assertTrue(result.ok)
        visit.refresh_from_db()
        self.assertIsNotNone(visit.checked_out_at)
        self.assertEqual(visit.checked_out_by, self.desk)
        self.assertFalse(services.check_out(visit, self.desk).ok)

    def test_close_stale_command(self):
        from django.core.management import call_command
        Attendance.objects.create(member=self.member, membership=self.membership, checked_in_at=timezone.now() - 2 * DAY)
        call_command("close_stale_visits", stdout=mock.MagicMock())
        self.assertTrue(Attendance.objects.get().auto_closed)


class QRTests(AttendanceBase):
    def test_pass_token_is_random_and_contains_no_personal_data(self):
        card = services.get_pass(self.member)
        other = services.get_pass(make_member())
        self.assertNotEqual(card.token, other.token)
        self.assertGreaterEqual(len(card.token), 32)
        payload = card.qr_payload
        for secret in (self.member.full_name, "Amaya", self.member.member_id, self.member.phone, str(self.member.pk)):
            self.assertNotIn(secret, payload.replace(card.token, ""))
        self.assertTrue(payload.startswith("BUILDME-CHECKIN:"))

    def test_qr_check_in(self):
        card = services.get_pass(self.member)
        result = services.check_in_by_qr(card.qr_payload, self.desk)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.attendance.method, Attendance.Method.QR)

    def test_bare_token_accepted(self):
        card = services.get_pass(self.member)
        self.assertTrue(services.check_in_by_qr(card.token, self.desk).ok)

    def test_invalid_qr_rejected_and_logged(self):
        for raw in ("", "BUILDME-CHECKIN:not-a-real-token-123456", "x" * 300, "<script>", self.member.member_id):
            result = services.check_in_by_qr(raw, self.desk)
            self.assertFalse(result.ok)
            self.assertEqual(result.message, "QR code not recognised. Check the member in manually or reissue their pass.")
        self.assertEqual(ActivityLog.objects.filter(action=ActivityLog.Action.CHECKIN_REJECTED).count(), 5)
        self.assertFalse(Attendance.objects.exists())

    def test_qr_still_enforces_membership_rules(self):
        lapsed = make_member(joined_on=self.today - 200 * DAY)
        make_membership(lapsed, self.plan, self.today - 80 * DAY)
        result = services.check_in_by_qr(services.get_pass(lapsed).qr_payload, self.desk)
        self.assertFalse(result.ok)
        self.assertIn("expired", result.message)

    def test_reissue_invalidates_old_code(self):
        old = services.get_pass(self.member).qr_payload
        services.reissue_pass(self.member, self.manager)
        self.assertFalse(services.check_in_by_qr(old, self.desk).ok)
        self.assertTrue(services.check_in_by_qr(services.get_pass(self.member).qr_payload, self.desk).ok)
        self.assertEqual(CheckInPass.objects.count(), 1)


class AttendanceViewTests(AttendanceBase):
    def test_desk_manual_and_qr_check_in(self):
        self.client.force_login(self.desk)
        response = self.client.get(reverse("attendance:desk"), {"q": "0771234567"})
        self.assertContains(response, "Amaya Silva")
        response = self.client.post(reverse("attendance:check_in"), {"member": self.member.pk}, follow=True)
        self.assertContains(response, "checked in at")
        self.assertContains(response, "Balance outstanding")
        response = self.client.post(reverse("attendance:check_in"), {"member": self.member.pk}, follow=True)
        self.assertContains(response, "already checked in")
        visit = Attendance.objects.get()
        self.client.post(reverse("attendance:check_out", args=[visit.pk]))
        token = services.get_pass(self.member).qr_payload
        response = self.client.post(reverse("attendance:check_in_qr"), {"token": token}, follow=True)
        self.assertContains(response, "checked in at")
        self.assertEqual(Attendance.objects.count(), 2)

    def test_open_redirect_blocked(self):
        self.client.force_login(self.desk)
        response = self.client.post(reverse("attendance:check_in"), {"member": self.member.pk, "next": "https://evil.example"})
        self.assertRedirects(response, reverse("attendance:desk"), fetch_redirect_response=False)

    def test_permissions(self):
        trainer = make_user(Role.TRAINER)  # can view attendance, can't record it
        self.client.force_login(trainer)
        self.assertEqual(self.client.get(reverse("attendance:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("attendance:daily")).status_code, 200)
        self.assertEqual(self.client.get(reverse("attendance:desk")).status_code, 403)
        self.assertEqual(self.client.post(reverse("attendance:check_in"), {"member": self.member.pk}).status_code, 403)
        self.assertEqual(self.client.post(reverse("attendance:reissue_pass", args=[self.member.pk])).status_code, 403)
        accountant = make_user(Role.ACCOUNTANT)  # no attendance access
        self.client.force_login(accountant)
        self.assertEqual(self.client.get(reverse("attendance:list")).status_code, 403)
        self.assertFalse(Attendance.objects.exists())

    def test_member_sees_own_pass_only(self):
        login = make_user(Role.MEMBER, username="amaya")
        login.member_profile.delete()
        self.member.user = login
        self.member.save()
        self.client.force_login(login)
        self.assertEqual(self.client.get(reverse("attendance:pass", args=[self.member.pk])).status_code, 200)
        other = make_member()
        self.assertEqual(self.client.get(reverse("attendance:pass", args=[other.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("attendance:list")).status_code, 403)
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, "My check-in pass")
        self.assertContains(response, "<svg")

    def test_history_filters_and_member_history(self):
        other = make_member(full_name="Bimal Perera")
        make_membership(other, self.plan, self.today)
        services.check_in(self.member, self.desk)
        services.check_in(other, self.desk)
        old = Attendance.objects.create(member=self.member, membership=self.membership,
                                        checked_in_at=timezone.now() - 20 * DAY, auto_closed=True)
        self.client.force_login(self.manager)
        visits = self.client.get(reverse("attendance:list")).context["visits"]
        self.assertEqual(len(visits), 2)  # default: last 7 days
        response = self.client.get(reverse("attendance:list"), {
            "member": self.member.pk, "date_from": (self.today - 30 * DAY).isoformat(), "date_to": self.today.isoformat()})
        self.assertEqual({v.pk for v in response.context["visits"]}, {old.pk, Attendance.objects.get(member=self.member, auto_closed=False).pk})
        response = self.client.get(reverse("attendance:list"), {"status": "open"})
        self.assertEqual(len(response.context["visits"]), 2)
        response = self.client.get(reverse("attendance:list"), {"q": "Bimal"})
        self.assertEqual([v.member for v in response.context["visits"]], [other])
        self.assertEqual(self.client.get(reverse("attendance:list"), {"date_from": "garbage"}).status_code, 200)

    def test_daily_report_and_dashboard(self):
        visit = services.check_in(self.member, self.desk).attendance
        services.check_out(visit, self.desk)
        services.check_in(self.member, self.desk)
        self.client.force_login(self.manager)
        response = self.client.get(reverse("attendance:daily"))
        stats = response.context["stats"]
        self.assertEqual((stats["visits"], stats["members"], stats["in_gym"]), (2, 1, 1))
        self.assertEqual(sum(response.context["chart"]["data"]), 2)
        yesterday = self.client.get(reverse("attendance:daily"), {"date": (self.today - DAY).isoformat()})
        self.assertEqual(yesterday.context["stats"]["visits"], 0)
        dash = self.client.get(reverse("dashboard:home"))
        stat = {s["slot"].key: s for s in dash.context["stats"]}["today_attendance"]
        self.assertEqual(stat["value"], 2)
        self.assertIn("1 in the gym now", stat["note"])

    def test_member_detail_shows_attendance(self):
        services.check_in(self.member, self.desk)
        self.client.force_login(self.manager)
        response = self.client.get(self.member.get_absolute_url())
        self.assertContains(response, "1 visit in 30 days")
        self.assertContains(response, reverse("attendance:pass", args=[self.member.pk]))
