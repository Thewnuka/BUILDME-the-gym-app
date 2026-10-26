from datetime import time, timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.appointments import services
from apps.appointments.models import Appointment, AppointmentEvent
from apps.core.models import Notification
from apps.memberships.tests.factories import make_member
from apps.payments import services as payment_services
from apps.payments.balances import balance_for
from apps.trainers.models import Availability, TimeOff, TrainerAssignment
from apps.trainers.tests.factories import (
    assign,
    at,
    make_active_member,
    make_member_with_login,
    make_trainer,
    session_type,
)


class BookingRuleTests(TestCase):
    def setUp(self):
        self.desk = make_user(Role.RECEPTIONIST)
        self.trainer = make_trainer()
        self.member = make_active_member()
        self.st = session_type(60)

    def book(self, start, member=None, trainer=None, st=None, **kw):
        return services.book(member or self.member, trainer or self.trainer, st or self.st, start, self.desk, **kw)

    def test_book_snapshot_and_event(self):
        paid = session_type(45, "2500.00", name="Premium")
        a = self.book(at(2, 10), st=paid)
        self.assertEqual(a.ends_at - a.starts_at, timedelta(minutes=45))
        self.assertEqual((a.fee, a.duration_minutes, a.status), (Decimal("2500.00"), 45, "booked"))
        paid.fee = Decimal("9999")
        paid.save()
        a.refresh_from_db()
        self.assertEqual(a.fee, Decimal("2500.00"))  # fee fixed at booking
        self.assertEqual(list(a.events.values_list("event_type", flat=True)), ["booked"])

    def test_trainer_overlap_prevented(self):
        self.book(at(2, 10))
        other = make_active_member()
        with self.assertRaisesMessage(services.BookingError, "just been booked"):
            self.book(at(2, 10, 30), member=other)  # overlaps 10:00–11:00
        self.book(at(2, 11), member=other)  # back-to-back is fine
        self.assertEqual(Appointment.objects.count(), 2)

    def test_member_double_booking_prevented(self):
        self.book(at(2, 10))
        with self.assertRaisesMessage(services.BookingError, "already has a session"):
            self.book(at(2, 10, 30), trainer=make_trainer())

    def test_availability_time_off_past_and_horizon(self):
        with self.assertRaisesMessage(services.BookingError, "not available"):
            self.book(at(2, 21, 30))  # would end 22:30, after the 22:00 window
        with self.assertRaisesMessage(services.BookingError, "not available"):
            self.book(at(2, 5))
        with self.assertRaisesMessage(services.BookingError, "future"):
            self.book(timezone.now() - timedelta(hours=1))
        with self.assertRaisesMessage(services.BookingError, "days ahead"):
            self.book(at(45, 10))
        day = (timezone.localdate() + timedelta(days=3))
        TimeOff.objects.create(trainer=self.trainer, start_date=day, end_date=day)
        with self.assertRaisesMessage(services.BookingError, "not working"):
            self.book(at(3, 10))

    def test_member_needs_membership_and_active_status(self):
        with self.assertRaisesMessage(services.BookingError, "no membership"):
            self.book(at(2, 10), member=make_member())
        with self.assertRaisesMessage(services.BookingError, "inactive"):
            from apps.members.models import Member
            m = make_active_member()
            Member.objects.filter(pk=m.pk).update(status="inactive", deactivated_on=timezone.localdate())
            m.refresh_from_db()
            self.book(at(2, 12), member=m)

    def test_inactive_trainer_or_session_type(self):
        self.st.is_active = False
        self.st.save()
        with self.assertRaisesMessage(services.BookingError, "no longer offered"):
            self.book(at(2, 10))
        self.st.is_active = True
        self.st.save()
        self.trainer.employment_status = "on_leave"
        self.trainer.save()
        with self.assertRaisesMessage(services.BookingError, "not taking bookings"):
            self.book(at(2, 10))

    def test_available_slots_exclude_busy_times(self):
        Availability.objects.filter(trainer=self.trainer).delete()
        day = timezone.localdate() + timedelta(days=2)
        Availability.objects.create(trainer=self.trainer, weekday=day.weekday(), start_time=time(9), end_time=time(12))
        self.book(at(2, 10))
        slots = [timezone.localtime(s).strftime("%H:%M") for s in services.available_slots(self.trainer, day, 60)]
        self.assertEqual(slots, ["09:00", "11:00"])

    def test_database_rejects_same_start_for_trainer(self):
        a = self.book(at(2, 10))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Appointment.objects.create(trainer=self.trainer, member=make_active_member(), session_type=self.st,
                                       starts_at=a.starts_at, ends_at=a.ends_at, duration_minutes=60)

    def test_simultaneous_booking_race_returns_friendly_error(self):
        with mock.patch("apps.appointments.services._check_slot", side_effect=IntegrityError("unique")):
            with self.assertRaisesMessage(services.BookingError, "just been booked"):
                self.book(at(2, 10))
        self.assertFalse(Appointment.objects.exists())

    def test_reschedule_and_cancel(self):
        a = self.book(at(2, 10))
        services.reschedule(a, at(3, 15), self.desk)
        a.refresh_from_db()
        self.assertEqual(timezone.localtime(a.starts_at).hour, 15)
        self.assertEqual(a.events.filter(event_type="rescheduled").count(), 1)
        with self.assertRaisesMessage(services.BookingError, "reason"):
            services.cancel(a, self.desk, "")
        services.cancel(a, self.desk, "Member ill")
        a.refresh_from_db()
        self.assertEqual(a.status, "cancelled")
        with self.assertRaisesMessage(services.BookingError, "Only booked"):
            services.reschedule(a, at(4, 10), self.desk)
        self.book(at(2, 10))  # cancelled slot is free again

    def test_outcome_only_after_start(self):
        a = self.book(at(2, 10))
        with self.assertRaisesMessage(services.BookingError, "once it has started"):
            services.mark_outcome(a, self.desk)
        Appointment.objects.filter(pk=a.pk).update(starts_at=timezone.now() - timedelta(hours=1),
                                                   ends_at=timezone.now())
        services.mark_outcome(a, self.desk, attended=True)
        a.refresh_from_db()
        self.assertEqual(a.status, "completed")

    def test_notifications_to_member_and_trainer(self):
        login, member = make_member_with_login("notified")
        self.book(at(2, 10), member=member)
        self.assertEqual(Notification.objects.filter(user=login, title="Session booked").count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.trainer.user, title="Session booked").count(), 1)

    def test_reminders_sent_once(self):
        login, member = make_member_with_login("remindme")
        a = self.book(at(2, 10), member=member)
        Appointment.objects.filter(pk=a.pk).update(starts_at=timezone.now() + timedelta(hours=3),
                                                   ends_at=timezone.now() + timedelta(hours=4))
        self.assertEqual(services.send_reminders(), 1)
        self.assertEqual(services.send_reminders(), 0)  # idempotent
        self.assertEqual(Notification.objects.filter(user=login, title="Upcoming session reminder").count(), 1)
        self.assertEqual(len(mail.outbox), 0)  # external channels are off unless NOTIFY_CHANNELS is set
        self.assertTrue(a.events.filter(event_type=AppointmentEvent.Type.REMINDED).exists())

    @override_settings(NOTIFY_CHANNELS=["email"])
    def test_reminder_email_when_channel_enabled(self):
        login, member = make_member_with_login("emailme")
        a = self.book(at(2, 10), member=member)
        Appointment.objects.filter(pk=a.pk).update(starts_at=timezone.now() + timedelta(hours=3),
                                                   ends_at=timezone.now() + timedelta(hours=4))
        self.assertEqual(len(mail.outbox), 1)  # the "Session booked" email
        mail.outbox.clear()
        services.send_reminders()
        self.assertEqual(len(mail.outbox), 1)
        delivery = Notification.objects.get(user=login, title="Upcoming session reminder").deliveries.get()
        self.assertEqual((delivery.channel, delivery.status), ("email", "sent"))


class SessionFeeTests(TestCase):
    def setUp(self):
        self.desk = make_user(Role.RECEPTIONIST)
        self.trainer = make_trainer()
        self.member = make_active_member()
        self.a = services.book(self.member, self.trainer, session_type(60, "3000"), at(2, 10), self.desk)

    def test_fee_paid_through_existing_ledger(self):
        payment, _ = payment_services.record_payment(self.a, Decimal("1000"), "cash", timezone.localdate(), self.desk)
        self.assertEqual((payment.appointment, payment.membership, payment.member), (self.a, None, self.member))
        self.assertTrue(payment.receipt_number.startswith("RCPT-"))
        b = balance_for(self.a)
        self.assertEqual((b.charge, b.outstanding, b.status), (Decimal("3000"), Decimal("2000"), "partially_paid"))
        with self.assertRaisesMessage(payment_services.PaymentError, "exceeds"):
            payment_services.record_payment(self.a, Decimal("2000.01"), "cash", timezone.localdate(), self.desk)

    def test_cancelled_session_charge_stops_and_refund_works(self):
        payment, _ = payment_services.record_payment(self.a, Decimal("3000"), "card", timezone.localdate(), self.desk)
        services.cancel(self.a, self.desk, "Trainer ill")
        self.a.refresh_from_db()
        self.assertEqual(balance_for(self.a).outstanding, Decimal("0"))
        manager = make_user(Role.GYM_MANAGER)
        refund, _ = payment_services.refund_payment(payment, Decimal("3000"), "card", timezone.localdate(), manager,
                                                    "Session cancelled by gym")
        self.assertEqual(refund.appointment, self.a)
        self.assertEqual(balance_for(self.a).status, "refunded")

    def test_payment_needs_exactly_one_target(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            from apps.payments.models import Payment
            Payment.objects.create(member=self.member, amount=1, method="cash", paid_on=timezone.localdate(),
                                   recorded_by=self.desk)

    def test_session_fee_pages(self):
        self.client.force_login(self.desk)
        url = reverse("payments:record_session", args=[self.a.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        import uuid
        response = self.client.post(url, {"amount": "3000", "method": "cash", "paid_on": timezone.localdate().isoformat(),
                                          "external_reference": "", "notes": "", "submission_token": str(uuid.uuid4())})
        payment = self.a.payments.get()
        self.assertRedirects(response, payment.get_absolute_url())
        receipt = self.client.get(reverse("payments:receipt", args=[payment.pk]))
        self.assertContains(receipt, "with " + self.trainer.name)
        self.assertEqual(self.client.get(reverse("payments:receipt_pdf", args=[payment.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("payments:outstanding")).status_code, 200)

    def test_outstanding_includes_unpaid_session_fees(self):
        self.client.force_login(make_user(Role.GYM_MANAGER))
        stats = {s["slot"].key: s for s in self.client.get(reverse("dashboard:home")).context["stats"]}
        self.assertIn("1 session fee", stats["outstanding_payments"]["note"])


class MemberPortalBookingTests(TestCase):
    def setUp(self):
        self.login, self.member = make_member_with_login("portal")
        self.trainer = make_trainer()
        self.other_trainer = make_trainer()
        self.st = session_type(60)
        assign(self.trainer, self.member)
        self.client.force_login(self.login)

    def post_booking(self, trainer, start):
        day = timezone.localtime(start).date().isoformat()
        return self.client.post(reverse("appointments:my_book"), {
            "trainer": trainer.pk, "session_type": self.st.pk, "day": day, "start": start.isoformat()})

    def test_member_books_assigned_trainer(self):
        response = self.post_booking(self.trainer, at(2, 10))
        a = Appointment.objects.get()
        self.assertRedirects(response, reverse("appointments:my_detail", args=[a.pk]))
        self.assertEqual((a.member, a.booked_by), (self.member, self.login))

    def test_member_cannot_book_unassigned_trainer(self):
        response = self.post_booking(self.other_trainer, at(2, 10))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Appointment.objects.exists())
        listing = self.client.get(reverse("appointments:my_book"))
        self.assertNotIn(self.other_trainer, list(listing.context["form"].fields["trainer"].queryset))

    def test_member_cannot_book_for_someone_else(self):
        other = make_active_member()
        assign(self.trainer, other)
        self.client.post(reverse("appointments:my_book"), {
            "member": other.pk, "trainer": self.trainer.pk, "session_type": self.st.pk,
            "day": at(2, 10).date().isoformat(), "start": at(2, 10).isoformat()})
        self.assertEqual(Appointment.objects.get().member, self.member)  # 'member' field ignored

    def test_member_change_cutoff(self):
        desk = make_user(Role.RECEPTIONIST)
        a = services.book(self.member, self.trainer, self.st, at(2, 10), desk)
        Appointment.objects.filter(pk=a.pk).update(starts_at=timezone.now() + timedelta(hours=5),
                                                   ends_at=timezone.now() + timedelta(hours=6))
        response = self.client.post(reverse("appointments:my_cancel", args=[a.pk]), {"reason": "Busy"}, follow=True)
        self.assertContains(response, "up to 24 hours before")
        a.refresh_from_db()
        self.assertEqual(a.status, "booked")
        services.cancel(a, desk, "Staff can still cancel")  # staff override
        a.refresh_from_db()
        self.assertEqual(a.status, "cancelled")

    def test_member_cancels_within_window(self):
        a = services.book(self.member, self.trainer, self.st, at(3, 10), self.login, as_member=True)
        self.client.post(reverse("appointments:my_cancel", args=[a.pk]), {"reason": "Travelling"})
        a.refresh_from_db()
        self.assertEqual(a.status, "cancelled")

    def test_member_sees_only_own_sessions(self):
        other = make_active_member()
        assign(self.trainer, other)
        theirs = services.book(other, self.trainer, self.st, at(2, 15), make_user(Role.RECEPTIONIST))
        self.assertEqual(self.client.get(reverse("appointments:my_detail", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("appointments:my_cancel", args=[theirs.pk]),
                                          {"reason": "x" * 5}).status_code, 404)
        for url in (reverse("appointments:list"), reverse("appointments:detail", args=[theirs.pk]),
                    reverse("appointments:book")):
            self.assertEqual(self.client.get(url).status_code, 403, url)


class StaffAppointmentViewTests(TestCase):
    def setUp(self):
        self.desk = make_user(Role.RECEPTIONIST)
        self.trainer = make_trainer()
        self.member = make_active_member()
        self.st = session_type(60)

    def test_staff_booking_flow(self):
        self.client.force_login(self.desk)
        day = at(2, 10).date().isoformat()
        response = self.client.get(reverse("appointments:book"), {"member": self.member.pk, "trainer": self.trainer.pk,
                                                                   "session_type": self.st.pk, "day": day})
        self.assertIn(at(2, 10), response.context["slots"])
        response = self.client.post(reverse("appointments:book"), {
            "member": self.member.pk, "trainer": self.trainer.pk, "session_type": self.st.pk, "day": day,
            "start": at(2, 10).isoformat()})
        a = Appointment.objects.get()
        self.assertRedirects(response, a.get_absolute_url())
        for url in (reverse("appointments:list"), a.get_absolute_url(), reverse("appointments:reschedule", args=[a.pk])):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_trainer_sees_only_own_schedule(self):
        mine = services.book(self.member, self.trainer, self.st, at(2, 10), self.desk)
        other_trainer = make_trainer()
        theirs = services.book(make_active_member(), other_trainer, self.st, at(2, 10), self.desk)
        self.client.force_login(self.trainer.user)
        listing = self.client.get(reverse("appointments:list"))
        self.assertEqual(list(listing.context["appointments"]), [mine])
        self.assertEqual(self.client.get(reverse("appointments:detail", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("appointments:book")).status_code, 403)

    def test_accountant_has_no_appointment_access(self):
        self.client.force_login(make_user(Role.ACCOUNTANT))
        self.assertEqual(self.client.get(reverse("appointments:list")).status_code, 403)

    def test_session_types_managed_by_managers_only(self):
        self.client.force_login(self.desk)
        self.assertEqual(self.client.get(reverse("appointments:session_types")).status_code, 403)
        self.client.force_login(make_user(Role.GYM_MANAGER))
        self.client.post(reverse("appointments:session_type_create"),
                         {"name": "Assessment", "duration_minutes": 30, "fee": "1500", "is_active": "on"})
        self.assertTrue(type(self.st).objects.filter(name="Assessment").exists())
        response = self.client.post(reverse("appointments:session_type_create"),
                                    {"name": "Bad", "duration_minutes": 30, "fee": "-1"})
        self.assertIn("fee", response.context["form"].errors)

    def test_notifications_page_is_private(self):
        login, member = make_member_with_login("notes")
        services.book(member, self.trainer, self.st, at(2, 10), self.desk)
        note = Notification.objects.get(user=login)
        self.client.force_login(self.trainer.user)
        self.assertEqual(self.client.post(reverse("notifications:open", args=[note.pk])).status_code, 404)
        self.client.force_login(login)
        self.assertContains(self.client.get(reverse("notifications:list")), "Session booked")
        self.client.post(reverse("notifications:read_all"))
        note.refresh_from_db()
        self.assertTrue(note.is_read)


@override_settings(APPOINTMENT_MEMBER_CHANGE_CUTOFF_HOURS=0)
class CutoffSettingTests(TestCase):
    def test_cutoff_is_configurable(self):
        login, member = make_member_with_login("flex")
        trainer = make_trainer()
        TrainerAssignment.objects.create(trainer=trainer, member=member)
        a = services.book(member, trainer, session_type(60), at(2, 10), login, as_member=True)
        Appointment.objects.filter(pk=a.pk).update(starts_at=timezone.now() + timedelta(hours=1),
                                                   ends_at=timezone.now() + timedelta(hours=2))
        a.refresh_from_db()
        services.cancel(a, login, "Changed plans", as_member=True)
