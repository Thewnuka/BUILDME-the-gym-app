"""End-to-end workflows A-H, driven through the real views as the staff and members would use them."""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.appointments.models import Appointment
from apps.attendance.models import Attendance
from apps.core import gym, scheduled
from apps.core.models import ActivityLog, GymSettings, Notification
from apps.dashboard import registry
from apps.expenses.models import Expense
from apps.members.models import Member
from apps.memberships.models import MembershipPlan
from apps.payments.balances import balance_for
from apps.payments.models import Payment
from apps.reports import finance
from apps.trainers.models import TrainerProfile
from apps.trainers.tests.factories import make_member_with_login, session_type
from apps.workouts.models import Exercise, WorkoutPlan

D = Decimal


def today():
    return timezone.localdate()


def stats(user):
    return {s["slot"].key: s["value"] for s in registry.stats_for(user)}


class WorkflowBase(TestCase):
    def setUp(self):
        self.admin = make_user(Role.SUPER_ADMIN)
        self.desk = make_user(Role.RECEPTIONIST)
        self.client.force_login(self.desk)

    def ok(self, response, expect=302):
        self.assertEqual(response.status_code, expect,
                         getattr(response, "context", None) and [
                             f.errors for f in response.context.get("form", []) and [response.context["form"]]])
        return response

    def make_plan(self, name="Monthly", price="6000"):
        self.client.force_login(self.admin)
        self.ok(self.client.post(reverse("memberships:plan_create"), {
            "name": name, "description": "", "duration_preset": "monthly", "duration_value": "", "duration_unit": "",
            "price": price, "benefits": "Gym floor", "access_restrictions": "", "is_active": "on"}))
        self.client.force_login(self.desk)
        return MembershipPlan.objects.get(name=name)

    def register(self, name="Nimal Silva", phone="0771234567", joined=None):
        self.ok(self.client.post(reverse("members:create"), {
            "full_name": name, "phone": phone, "email": f"{phone}@example.com", "date_of_birth": "1995-04-02",
            "address": "Colombo", "emergency_contact_name": "", "emergency_contact_phone": "",
            "joined_on": (joined or today()).isoformat(), "fitness_goals": ""}))
        return Member.objects.get(full_name=name)

    def assign(self, member, plan, start=None):
        self.ok(self.client.post(reverse("memberships:assign", args=[member.pk]),
                                 {"plan": plan.pk, "start_date": (start or today()).isoformat(), "notes": ""}))
        return member.memberships.order_by("-start_date").first()

    def pay(self, target_url, amount, method="cash"):
        return self.client.post(target_url, {"amount": amount, "method": method, "paid_on": today().isoformat(),
                                             "external_reference": "", "notes": "",
                                             "submission_token": str(uuid.uuid4())})


class WorkflowA_Registration(WorkflowBase):
    def test_register_assign_pay_receipt_status(self):
        plan = self.make_plan()
        member = self.register()
        self.assertTrue(member.member_id)
        ms = self.assign(member, plan)
        self.assertEqual((ms.status, ms.price, ms.end_date), ("active", D("6000"), ms.end_date))
        self.assertGreater(ms.end_date, today())
        self.ok(self.pay(reverse("payments:record", args=[ms.pk]), "6000"))
        payment = Payment.objects.get(membership=ms)
        self.assertTrue(payment.receipt_number)
        receipt = self.client.get(reverse("payments:receipt", args=[payment.pk]))
        self.assertContains(receipt, payment.receipt_number)
        self.assertContains(receipt, "LKR 6,000.00")
        pdf = self.client.get(reverse("payments:receipt_pdf", args=[payment.pk]))
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertEqual(balance_for(ms).outstanding, D("0"))
        detail = self.client.get(member.get_absolute_url())
        self.assertContains(detail, "Active")


class WorkflowB_Attendance(WorkflowBase):
    def test_find_validate_check_in_history(self):
        plan = self.make_plan()
        member = self.register()
        unpaid_stranger = self.register("No Plan", "0779999999")
        self.assign(member, plan)
        found = self.client.get(reverse("attendance:desk"), {"q": "Nimal"})
        self.assertIn(member, list(found.context["results"]))
        self.client.post(reverse("attendance:check_in"), {"member": member.pk})
        self.assertEqual(Attendance.objects.filter(member=member).count(), 1)
        # A member without a valid membership is refused and the refusal is audited.
        refused = self.client.post(reverse("attendance:check_in"), {"member": unpaid_stranger.pk}, follow=True)
        self.assertFalse(Attendance.objects.filter(member=unpaid_stranger).exists())
        self.assertIn("membership", " ".join(str(m) for m in refused.context["messages"]).lower())
        # Duplicate check-in while still inside is prevented.
        self.client.post(reverse("attendance:check_in"), {"member": member.pk})
        self.assertEqual(Attendance.objects.filter(member=member).count(), 1)
        history = self.client.get(reverse("attendance:list"), {"q": member.member_id})
        self.assertContains(history, member.full_name)
        # Unauthorised roles can't record attendance.
        self.client.force_login(make_user(Role.ACCOUNTANT))
        self.assertEqual(self.client.post(reverse("attendance:check_in"), {"member": member.pk}).status_code, 403)
        login, _ = make_member_with_login("intruder")
        self.client.force_login(login)
        self.assertEqual(self.client.get(reverse("attendance:desk")).status_code, 403)


class WorkflowC_Renewal(WorkflowBase):
    def test_renewal_keeps_history_and_updates_dashboard(self):
        plan = self.make_plan()
        member = self.register(joined=today() - timedelta(days=30))
        first = self.assign(member, plan, start=today() - timedelta(days=20))
        self.ok(self.client.post(reverse("memberships:renew", args=[first.pk]), {"plan": plan.pk, "notes": ""}))
        periods = list(member.memberships.order_by("start_date"))
        self.assertEqual(len(periods), 2)
        old, new = periods
        self.assertEqual(old.pk, first.pk)
        self.assertEqual(old.end_date, first.end_date)              # history untouched
        self.assertEqual(new.start_date, old.end_date + timedelta(days=1))
        self.assertEqual(new.kind, "renewal")
        self.assertEqual(new.price, D("6000"))
        self.assertTrue(Notification.objects.filter(member=member, title="Membership renewed").exists())
        s = stats(self.admin)
        self.assertEqual(s["active_members"], 1)
        self.assertEqual(s["outstanding_payments"], D("12000"))  # both periods unpaid, never counted as revenue
        self.assertEqual(s["monthly_revenue"], D("0"))


class WorkflowD_PartialPayments(WorkflowBase):
    def test_two_part_payments(self):
        plan = self.make_plan(price="10000")
        ms = self.assign(self.register(), plan)
        url = reverse("payments:record", args=[ms.pk])
        self.ok(self.pay(url, "4000"))
        self.assertEqual(balance_for(ms).outstanding, D("6000"))
        self.assertEqual(self.pay(url, "6000.01").status_code, 200)  # overpayment refused
        self.ok(self.pay(url, "2500.50", method="card"))
        bal = balance_for(ms)
        self.assertEqual((bal.net_paid, bal.outstanding), (D("6500.50"), D("3499.50")))
        self.assertEqual(Payment.objects.filter(membership=ms).count(), 2)
        self.assertEqual(finance.outstanding_now()["memberships"], D("3499.50"))


class WorkflowE_PersonalTraining(WorkflowBase):
    def test_trainer_availability_booking_overlap_completion(self):
        manager = make_user(Role.GYM_MANAGER)
        self.client.force_login(manager)
        self.ok(self.client.post(reverse("trainers:create"), {
            "first_name": "Amal", "last_name": "Perera", "username": "amal", "email": "amal@example.com",
            "phone": "0711111111", "specialties": "Strength", "employment_type": "full_time",
            "hired_on": today().isoformat()}))
        trainer = TrainerProfile.objects.get(user__username="amal")
        self.assertEqual(len(mail.outbox), 1)  # set-your-password email
        day = today() + timedelta(days=2)
        self.ok(self.client.post(reverse("trainers:availability_add", args=[trainer.pk]),
                                 {"weekday": day.weekday(), "start_time": "08:00", "end_time": "12:00"}))
        self.assertEqual(trainer.availability.count(), 1)
        pt = session_type(60, "2500")
        plan = self.make_plan()
        a, b = self.register("Member A", "0772000001"), self.register("Member B", "0772000002")
        self.assign(a, plan)
        self.assign(b, plan)
        self.client.force_login(manager)
        start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time().replace(hour=9)))
        book = {"trainer": trainer.pk, "session_type": pt.pk, "day": day.isoformat(), "start": start.isoformat()}
        self.ok(self.client.post(reverse("appointments:book"), {**book, "member": a.pk}))
        appt = Appointment.objects.get(member=a)
        self.assertEqual((appt.fee, appt.status), (D("2500"), "booked"))
        # Same trainer, overlapping time: refused.
        self.client.post(reverse("appointments:book"), {**book, "member": b.pk,
                                                         "start": (start + timedelta(minutes=30)).isoformat()})
        self.assertFalse(Appointment.objects.filter(member=b).exists())
        # Outside availability: refused.
        self.client.post(reverse("appointments:book"), {**book, "member": b.pk,
                                                         "start": (start + timedelta(hours=5)).isoformat()})
        self.assertFalse(Appointment.objects.filter(member=b).exists())
        # Completion only once the session has started.
        self.client.post(reverse("appointments:outcome", args=[appt.pk]), {"outcome": "attended"})
        appt.refresh_from_db()
        self.assertEqual(appt.status, "booked")
        Appointment.objects.filter(pk=appt.pk).update(starts_at=timezone.now() - timedelta(hours=2),
                                                      ends_at=timezone.now() - timedelta(hours=1))
        self.client.force_login(trainer.user)  # the session's own trainer records it
        self.ok(self.client.post(reverse("appointments:outcome", args=[appt.pk]), {"outcome": "attended"}))
        appt.refresh_from_db()
        self.assertEqual(appt.status, "completed")


class WorkflowF_WorkoutPlans(WorkflowBase):
    def test_exercise_plan_assign_member_read_only(self):
        manager = make_user(Role.GYM_MANAGER)
        login, member = make_member_with_login("plantaker")
        self.client.force_login(manager)
        self.ok(self.client.post(reverse("workouts:exercise_create"), {
            "name": "Goblet squat", "muscle_group": "legs", "category": "strength", "description": "",
            "instructions": "Keep chest up.", "video_url": "", "is_active": "on"}))
        exercise = Exercise.objects.get(name="Goblet squat")
        self.ok(self.client.post(reverse("workouts:plan_create"), {
            "name": "Beginner strength", "goal": "Strength", "description": "", "start_date": "", "end_date": ""}))
        template = WorkoutPlan.objects.get(name="Beginner strength", member__isnull=True)
        self.client.post(reverse("workouts:day_add", args=[template.pk]), {"day_number": "1", "title": "Legs"})
        day = template.days.get()
        self.client.post(reverse("workouts:item_add", args=[template.pk, day.pk]),
                         {"exercise": exercise.pk, "sets": "3", "reps": "10", "order": "1"})
        self.assertEqual(day.items.count(), 1)
        self.ok(self.client.post(reverse("workouts:plan_assign", args=[template.pk]),
                                 {"member": member.pk, "start_date": today().isoformat()}))
        copy = WorkoutPlan.objects.get(member=member)
        self.assertEqual(copy.days.get().items.get().exercise, exercise)
        # The member sees the plan and the exercise instructions...
        self.client.force_login(login)
        self.assertContains(self.client.get(reverse("workouts:my")), "Beginner strength")
        self.assertContains(self.client.get(copy.get_absolute_url()), "Goblet squat")
        self.assertContains(self.client.get(exercise.get_absolute_url()), "Keep chest up.")
        # ...but cannot change it, the template, or the library.
        item = copy.days.get().items.get()
        self.assertEqual(self.client.post(reverse("workouts:item_update", args=[copy.pk, item.pk]),
                                          {"exercise": exercise.pk, "sets": "9", "reps": "99", "order": "1"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("workouts:plan_update", args=[copy.pk])).status_code, 403)
        self.assertEqual(self.client.get(template.get_absolute_url()).status_code, 403)
        self.assertEqual(self.client.get(reverse("workouts:exercise_update", args=[exercise.pk])).status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.sets, 3)
        # Exercises outside the member's plans stay private.
        other = Exercise.objects.create(name="Snatch", muscle_group="full_body", category="strength")
        self.assertEqual(self.client.get(other.get_absolute_url()).status_code, 403)
        # Another member can't see this member's plan.
        stranger, _ = make_member_with_login("stranger")
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(copy.get_absolute_url()).status_code, 403)


class WorkflowG_ExpensesAndReports(WorkflowBase):
    def test_expense_and_payment_flow_into_reports_and_dashboard(self):
        plan = self.make_plan(price="8000")
        ms = self.assign(self.register(), plan)
        self.ok(self.pay(reverse("payments:record", args=[ms.pk]), "8000"))
        accountant = make_user(Role.ACCOUNTANT)
        self.client.force_login(accountant)
        self.ok(self.client.post(reverse("expenses:create"), {
            "category": "electricity", "amount": "3000", "expense_date": today().isoformat(),
            "description": "CEB bill", "supplier": "", "invoice_number": ""}))
        self.assertEqual(Expense.objects.get().recorded_by, accountant)
        period = {"date_from": today().replace(day=1).isoformat(), "date_to": today().isoformat()}
        report = self.client.get(reverse("reports:report", args=["revenue-vs-expenses"]), period)
        summary = dict(report.context["summary"])
        self.assertEqual(summary["Net collected"], "LKR 8,000.00")
        self.assertEqual(summary["Operating expenses"], "LKR 3,000.00")
        self.assertEqual(summary["Est. operating profit"], "LKR 5,000.00")
        s = stats(self.admin)
        self.assertEqual((s["monthly_revenue"], s["expenses_month"], s["profit_month"]),
                         (D("8000"), D("3000"), D("5000")))


class WorkflowH_Notifications(WorkflowBase):
    def test_expiry_reminder_once_in_member_centre(self):
        plan = self.make_plan()
        login, member = make_member_with_login("expiring")
        member.memberships.all().delete()
        ms = self.assign(member, plan, start=today() - timedelta(days=25))
        days_left = (ms.end_date - today()).days
        with self.settings(MEMBERSHIP_EXPIRY_REMINDER_DAYS=[days_left]):
            self.assertEqual(scheduled.membership_expiry_reminders(), 1)
            self.assertEqual(scheduled.membership_expiry_reminders(), 0)
            scheduled.run_all()
        reminders = Notification.objects.filter(member=member, title__startswith="Your membership ends")
        self.assertEqual(reminders.count(), 1)
        self.assertEqual(reminders.get().user, login)
        self.client.force_login(login)
        self.assertContains(self.client.get(reverse("notifications:list")), "Your membership ends")
        # Staff don't see it in their own centre.
        self.client.force_login(self.desk)
        self.assertNotContains(self.client.get(reverse("notifications:list")), "Your membership ends")


class GymSettingsTests(WorkflowBase):
    def test_only_settings_permission_can_edit_and_values_apply(self):
        for role in (Role.GYM_MANAGER, Role.RECEPTIONIST, Role.ACCOUNTANT, Role.TRAINER, Role.MEMBER):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(reverse("settings")).status_code, 403, role)
            self.assertEqual(self.client.post(reverse("settings"), {"gym_name": "Hacked"}).status_code, 403, role)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("settings")).status_code, 200)
        self.assertEqual(gym.gym_name(), "BUILDME")
        self.ok(self.client.post(reverse("settings"), {
            "gym_name": "BUILDME Kandy", "address": "12 Temple Rd\nKandy", "phone": "0812222222",
            "email": "kandy@example.com", "website": "", "business_registration": "PV 1234",
            "receipt_footer": "Thank you for training with us!", "membership_expiry_reminder_days": "1, 14,7",
            "payment_due_reminder_days": "5", "expiring_soon_days": "", "equipment_maintenance_reminder_days": "",
            "appointment_reminder_hours": ""}))
        row = GymSettings.objects.get()
        self.assertEqual((row.membership_expiry_reminder_days, row.updated_by), ("14,7,1", self.admin))
        self.assertEqual(gym.gym_name(), "BUILDME Kandy")
        self.assertEqual(gym.membership_expiry_reminder_days(), [14, 7, 1])
        self.assertEqual(gym.payment_due_reminder_days(), 5)
        self.assertEqual(gym.expiring_soon_days(), 7)  # blank = server default
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.SETTINGS_UPDATED).exists())
        self.assertContains(self.client.get(reverse("dashboard:home")), "BUILDME Kandy")
        plan = self.make_plan()
        ms = self.assign(self.register(), plan)
        self.client.force_login(self.desk)
        self.pay(reverse("payments:record", args=[ms.pk]), "100")
        receipt = self.client.get(reverse("payments:receipt", args=[Payment.objects.get().pk]))
        self.assertContains(receipt, "Thank you for training with us!")
        self.assertContains(receipt, "Temple Rd")

    def test_validation(self):
        self.client.force_login(self.admin)
        for bad in ({"membership_expiry_reminder_days": "7,x"}, {"membership_expiry_reminder_days": "0"},
                    {"payment_due_reminder_days": "0"}, {"appointment_reminder_hours": "1000"},
                    {"email": "not-an-email"}):
            self.assertEqual(self.client.post(reverse("settings"), bad).status_code, 200, bad)
        self.assertFalse(GymSettings.objects.exclude(membership_expiry_reminder_days="").exists())


