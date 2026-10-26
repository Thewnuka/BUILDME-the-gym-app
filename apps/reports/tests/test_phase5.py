import io
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.announcements import services as announce
from apps.announcements.models import Announcement
from apps.core import channels, scheduled
from apps.core.models import Notification, NotificationDelivery
from apps.core.notify import notify
from apps.equipment import services as equip
from apps.equipment.models import Equipment, FaultReport
from apps.expenses.models import Expense
from apps.inventory import services as stock
from apps.inventory.models import InventoryItem, StockMovement
from apps.memberships import services as membership_services
from apps.memberships.tests.factories import make_member, make_membership, make_plan
from apps.payments import services as pay
from apps.reports import finance
from apps.trainers.tests.factories import make_member_with_login

D = Decimal
TODAY = timezone.localdate


def expense(amount, category="rent", days_ago=0, status="active", **kw):
    e = Expense.objects.create(category=category, amount=D(amount), expense_date=TODAY() - timedelta(days=days_ago),
                               description=f"{category} {amount}", recorded_by=kw.pop("by", None) or make_user(Role.ACCOUNTANT), **kw)
    if status == "voided":
        Expense.objects.filter(pk=e.pk).update(status="voided", void_reason="x", voided_at=timezone.now())
    return e


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
class ExpenseTests(TestCase):
    def setUp(self):
        self.accountant = make_user(Role.ACCOUNTANT)
        self.manager = make_user(Role.GYM_MANAGER)

    def post_expense(self, **overrides):
        data = {"category": "electricity", "amount": "45000", "expense_date": TODAY().isoformat(),
                "description": "CEB bill October", "supplier": "", "invoice_number": "CEB-123"}
        data.update(overrides)
        return self.client.post(reverse("expenses:create"), data)

    def test_accountant_records_manager_views_only(self):
        self.client.force_login(self.accountant)
        self.post_expense()
        e = Expense.objects.get()
        self.assertEqual((e.recorded_by, e.source, e.reference), (self.accountant, "manual", f"EXP-{e.pk:06d}"))
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("expenses:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("expenses:create")).status_code, 403)
        self.assertEqual(self.post_expense().status_code, 403)
        self.assertEqual(self.client.post(reverse("expenses:void", args=[e.pk]), {"reason": "abc"}).status_code, 403)
        for role in (Role.RECEPTIONIST, Role.TRAINER, Role.MEMBER):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(reverse("expenses:list")).status_code, 403, role)
            self.assertEqual(self.client.get(e.get_absolute_url()).status_code, 403, role)
        self.assertEqual(Expense.objects.count(), 1)

    def test_validation(self):
        self.client.force_login(self.accountant)
        for bad in ({"amount": "0"}, {"amount": "-5"}, {"expense_date": (TODAY() + timedelta(days=1)).isoformat()},
                    {"description": "paid card 4111 1111 1111 1111"}, {"category": "bribes"}):
            self.assertEqual(self.post_expense(**bad).status_code, 200, bad)
        self.assertFalse(Expense.objects.exists())

    def test_receipt_upload_checked_and_protected(self):
        self.client.force_login(self.accountant)
        self.post_expense(receipt=SimpleUploadedFile("bill.pdf", b"not a pdf"))
        self.assertFalse(Expense.objects.exists())
        self.post_expense(receipt=SimpleUploadedFile("bill.pdf", b"%PDF-1.4\n%%EOF"))
        e = Expense.objects.get()
        self.assertEqual(self.client.get(reverse("expenses:receipt", args=[e.pk])).status_code, 200)
        self.client.force_login(make_user(Role.RECEPTIONIST))
        self.assertEqual(self.client.get(reverse("expenses:receipt", args=[e.pk])).status_code, 403)

    def test_void_keeps_record_and_excludes_from_totals(self):
        e = expense("1000", by=self.accountant)
        expense("500", by=self.accountant)
        self.client.force_login(self.accountant)
        self.client.post(reverse("expenses:void", args=[e.pk]), {"reason": "Duplicate"})
        e.refresh_from_db()
        self.assertEqual(e.status, "voided")
        self.assertEqual(self.client.get(reverse("expenses:list")).context["total"], D("500"))
        self.assertEqual(finance.expenses(TODAY(), TODAY())["operating"], D("500"))

    def test_linked_expense_not_editable(self):
        self.client.force_login(make_user(Role.GYM_MANAGER))
        self.client.post(reverse("equipment:create"), {"code": "", "name": "Treadmill", "category": "cardio",
                                                       "purchase_cost": "450000", "purchase_date": TODAY().isoformat(),
                                                       "condition": "excellent", "status": "available"})
        linked = Expense.objects.get()
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get(reverse("expenses:update", args=[linked.pk])).status_code, 403)


# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------
class EquipmentTests(TestCase):
    def setUp(self):
        self.manager = make_user(Role.GYM_MANAGER)
        self.client.force_login(self.manager)

    def create(self, **overrides):
        data = {"code": "", "name": "Treadmill X", "category": "cardio", "location": "Cardio zone",
                "purchase_date": TODAY().isoformat(), "purchase_cost": "450000", "condition": "excellent",
                "status": "available", "maintenance_interval_days": "90"}
        data.update(overrides)
        self.client.post(reverse("equipment:create"), data)
        return Equipment.objects.order_by("-pk").first()

    def test_register_with_auto_code_and_single_linked_expense(self):
        eq = self.create()
        self.assertEqual(eq.code, "EQ-0001")
        self.assertEqual(eq.next_maintenance_date, TODAY() + timedelta(days=90))
        self.assertEqual(Expense.objects.filter(source="equipment").count(), 1)
        self.assertEqual(eq.purchase_expense.category, "equipment")
        self.client.post(reverse("equipment:update", args=[eq.pk]), {
            "code": eq.code, "name": "Renamed", "category": "cardio", "purchase_cost": "999999",
            "condition": "good", "status": "available"})
        eq.refresh_from_db()
        self.assertEqual(eq.purchase_cost, D("450000"))  # locked once booked as an expense
        self.assertEqual(Expense.objects.count(), 1)
        dup = self.create(code="eq-0001")
        self.assertEqual(Equipment.objects.count(), 1)  # duplicate code rejected
        self.assertEqual(dup, eq)

    def test_fault_maintenance_workflow(self):
        eq = self.create(purchase_cost="")
        trainer = make_user(Role.TRAINER)
        self.client.force_login(trainer)
        self.client.post(reverse("equipment:report_fault", args=[eq.pk]),
                         {"severity": "unsafe", "description": "Belt snapped"})
        fault = FaultReport.objects.get()
        eq.refresh_from_db()
        self.assertEqual(eq.status, "out_of_service")
        self.assertTrue(Notification.objects.filter(user=self.manager, category="equipment").exists())
        self.assertFalse(Notification.objects.filter(user=trainer).exists())
        self.assertEqual(self.client.post(reverse("equipment:record_maintenance", args=[eq.pk]), {}).status_code, 403)
        self.client.force_login(self.manager)
        self.client.post(reverse("equipment:record_maintenance", args=[eq.pk]), {
            "kind": "repair", "performed_on": TODAY().isoformat(), "description": "Replaced belt", "cost": "12500",
            "performed_by": "FitFix Ltd", "status_after": "available", "resolves": fault.pk})
        eq.refresh_from_db()
        fault.refresh_from_db()
        record = eq.maintenance_records.get()
        self.assertEqual((eq.status, fault.status, record.fault), ("available", "resolved", fault))
        self.assertEqual(record.expense.amount, D("12500"))
        self.assertEqual(record.expense.category, "maintenance")
        self.assertEqual(eq.next_maintenance_date, TODAY() + timedelta(days=90))

    def test_detail_with_maintenance_without_contractor(self):
        eq = self.create(purchase_cost="")
        self.client.post(reverse("equipment:record_maintenance", args=[eq.pk]), {
            "kind": "service", "performed_on": TODAY().isoformat(), "description": "Lubricated", "cost": "",
            "performed_by": "", "status_after": "available"})
        self.assertEqual(eq.maintenance_records.count(), 1)
        self.assertContains(self.client.get(eq.get_absolute_url()), "Lubricated")

    def test_permissions(self):
        eq = self.create(purchase_cost="")
        for role in (Role.RECEPTIONIST, Role.ACCOUNTANT, Role.TRAINER):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(eq.get_absolute_url()).status_code, 200, role)
            self.assertEqual(self.client.get(reverse("equipment:create")).status_code, 403, role)
            self.assertEqual(self.client.post(reverse("equipment:set_status", args=[eq.pk]),
                                              {"status": "in_use"}).status_code, 403)
        self.client.force_login(make_user(Role.MEMBER))
        self.assertEqual(self.client.get(reverse("equipment:list")).status_code, 403)

    def test_maintenance_reminders_deduplicated(self):
        eq = self.create(purchase_cost="")
        Equipment.objects.filter(pk=eq.pk).update(next_maintenance_date=TODAY() + timedelta(days=2))
        self.assertEqual(equip.send_maintenance_reminders(), 1)
        self.assertEqual(equip.send_maintenance_reminders(), 0)
        Equipment.objects.filter(pk=eq.pk).update(next_maintenance_date=TODAY() - timedelta(days=1))
        self.assertEqual(equip.send_maintenance_reminders(), 1)  # new due date re-arms the reminder


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
class InventoryTests(TestCase):
    def setUp(self):
        self.manager = make_user(Role.GYM_MANAGER)
        self.item = InventoryItem.objects.create(sku="WAT-500", name="Water 500ml", category="drinks", unit="bottles",
                                                 minimum_stock=D("10"))

    def test_purchase_usage_and_ledger(self):
        m = stock.record_movement(self.item, "purchase", D("24"), self.manager, unit_cost=D("80"), reference="INV-9")
        self.item.refresh_from_db()
        self.assertEqual((self.item.quantity, m.quantity_after, m.total_cost), (D("24"), D("24"), D("1920.00")))
        self.assertEqual(m.expense.amount, D("1920.00"))
        self.assertEqual(m.expense.category, "supplies")
        stock.record_movement(self.item, "usage", D("5"), self.manager)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, D("19"))
        self.assertEqual(list(self.item.movements.values_list("quantity_after", flat=True)), [D("19"), D("24")])

    def test_cannot_go_negative(self):
        stock.record_movement(self.item, "purchase", D("3"), self.manager)
        with self.assertRaisesMessage(stock.StockError, "Not enough stock"):
            stock.record_movement(self.item, "sale", D("4"), self.manager)
        with self.assertRaisesMessage(stock.StockError, "Not enough stock"):
            stock.record_movement(self.item, "adjustment", D("-5"), self.manager, reason="count")
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventoryItem.objects.filter(pk=self.item.pk).update(quantity=D("-1"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockMovement.objects.create(item=self.item, kind="usage", quantity_change=D("1"), quantity_after=D("4"),
                                         recorded_by=self.manager)  # usage must be negative
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, D("3"))

    def test_adjustment_needs_reason_and_zero_rejected(self):
        with self.assertRaisesMessage(stock.StockError, "reason"):
            stock.record_movement(self.item, "adjustment", D("2"), self.manager)
        with self.assertRaisesMessage(stock.StockError, "greater than zero"):
            stock.record_movement(self.item, "purchase", D("0"), self.manager)

    def test_low_stock_alert_once_and_rearms(self):
        stock.record_movement(self.item, "purchase", D("15"), self.manager)
        stock.record_movement(self.item, "usage", D("6"), self.manager)   # 9 <= 10 → alert
        stock.record_movement(self.item, "usage", D("1"), self.manager)   # still low → no new alert
        alerts = Notification.objects.filter(user=self.manager, category="inventory")
        self.assertEqual(alerts.count(), 1)
        stock.record_movement(self.item, "purchase", D("20"), self.manager)  # recovered → re-armed
        stock.record_movement(self.item, "usage", D("20"), self.manager)     # low again → second alert
        self.assertEqual(alerts.count(), 2)

    def test_staff_can_only_use_stock(self):
        stock.record_movement(self.item, "purchase", D("10"), self.manager)
        desk = make_user(Role.RECEPTIONIST)
        self.client.force_login(desk)
        url = reverse("inventory:movement", args=[self.item.pk])
        self.client.post(url, {"kind": "usage", "quantity": "2", "reason": "towel service"})
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, D("8"))
        self.client.post(url, {"kind": "purchase", "quantity": "100", "unit_cost": "1"})
        self.client.post(url, {"kind": "adjustment", "quantity": "50", "reason": "found"})
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, D("8"))
        self.assertEqual(self.client.get(reverse("inventory:create")).status_code, 403)
        self.client.force_login(make_user(Role.MEMBER))
        self.assertEqual(self.client.get(reverse("inventory:list")).status_code, 403)


# ---------------------------------------------------------------------------
# Notifications, reminders and channels
# ---------------------------------------------------------------------------
class NotificationTests(TestCase):
    def setUp(self):
        self.login, self.member = make_member_with_login("notifyme")
        self.member.memberships.all().delete()

    def test_dedupe(self):
        self.assertIsNotNone(notify(user=self.login, title="Hi", dedupe_key="k1"))
        self.assertIsNone(notify(user=self.login, title="Hi again", dedupe_key="k1"))
        self.assertEqual(Notification.objects.count(), 1)

    def test_scheduled_reminders_are_not_duplicated(self):
        plan = make_plan()
        make_membership(self.member, plan, TODAY() - timedelta(days=27))         # ends in a few days, unpaid
        other = make_member(joined_on=TODAY() - timedelta(days=200))
        make_membership(other, plan, TODAY() - timedelta(days=33))               # expired a few days ago, not renewed
        first = scheduled.run_all()
        second = scheduled.run_all()
        self.assertGreaterEqual(first["membership_expiring"], 1)
        self.assertEqual(first["membership_expired"], 1)
        self.assertEqual(first["payment_due"], 1)
        self.assertEqual((second["membership_expiring"], second["membership_expired"], second["payment_due"]), (0, 0, 0))
        titles = list(Notification.objects.filter(member=self.member).values_list("title", flat=True))
        self.assertIn("Membership payment due", titles)
        self.assertTrue(any(t.startswith("Your membership ends") for t in titles))
        # The member without a login still gets a (non in-app) record for external channels.
        self.assertTrue(Notification.objects.filter(member=other, user__isnull=True).exists())

    def test_payment_and_renewal_notifications(self):
        staff = make_user(Role.GYM_MANAGER)
        m = make_membership(self.member, make_plan(), TODAY() - timedelta(days=3))
        pay.record_payment(m, D("1000"), "cash", TODAY(), staff)
        self.assertTrue(Notification.objects.filter(user=self.login, title="Payment received").exists())
        pending, _ = pay.record_payment(m, D("500"), "bank_transfer", TODAY(), staff, pending=True)
        self.assertEqual(Notification.objects.filter(title="Payment received").count(), 1)  # not until confirmed
        pay.confirm_payment(pending, staff)
        self.assertEqual(Notification.objects.filter(title="Payment received").count(), 2)
        membership_services.renew_membership(self.member, m.plan, staff)
        self.assertTrue(Notification.objects.filter(user=self.login, title="Membership renewed").exists())

    def test_no_external_delivery_without_configuration(self):
        notify(member=self.member, title="Test", external=True)
        self.assertFalse(NotificationDelivery.objects.exists())

    @override_settings(NOTIFY_CHANNELS=["email", "sms", "whatsapp"], SMS_PROVIDER="", WHATSAPP_PROVIDER="")
    def test_channels_without_providers_are_marked_not_sent(self):
        note = notify(member=self.member, title="Closure tomorrow", external=True)
        statuses = dict(note.deliveries.values_list("channel", "status"))
        self.assertEqual(statuses, {"email": "sent", "sms": "skipped", "whatsapp": "skipped"})
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(NOTIFY_CHANNELS=["email"], EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_dev_email_backend_not_claimed_as_sent(self):
        note = notify(member=self.member, title="Hello", external=True)
        d = note.deliveries.get()
        self.assertEqual(d.status, "skipped")
        self.assertIn("not delivered", d.detail)

    @override_settings(NOTIFY_CHANNELS=["sms"], SMS_PROVIDER="twilio", TWILIO_ACCOUNT_SID="AC1", TWILIO_AUTH_TOKEN="secret",
                       TWILIO_SMS_FROM="+15550001111", PUBLIC_BASE_URL="https://gym.example.com")
    def test_twilio_queued_then_delivered_via_signed_webhook(self):
        with mock.patch.object(channels, "_http", return_value={"sid": "SM123", "status": "queued"}) as http:
            note = notify(member=self.member, title="Reminder", external=True)
        d = note.deliveries.get()
        self.assertEqual((d.status, d.provider_message_id), ("queued", "SM123"))
        self.assertEqual(http.call_args[0][1]["StatusCallback"], "https://gym.example.com/notifications/webhooks/twilio/")
        url = reverse("notifications:twilio_webhook")
        params = {"MessageSid": "SM123", "MessageStatus": "delivered"}
        self.assertEqual(self.client.post(url, params, HTTP_X_TWILIO_SIGNATURE="forged").status_code, 403)
        d.refresh_from_db()
        self.assertEqual(d.status, "queued")
        import base64, hashlib, hmac
        payload = "https://gym.example.com" + url + "".join(f"{k}{params[k]}" for k in sorted(params))
        sig = base64.b64encode(hmac.new(b"secret", payload.encode(), hashlib.sha1).digest()).decode()
        self.assertEqual(self.client.post(url, params, HTTP_X_TWILIO_SIGNATURE=sig).status_code, 204)
        d.refresh_from_db()
        self.assertEqual(d.status, "delivered")

    @override_settings(NOTIFY_CHANNELS=["sms"], SMS_PROVIDER="twilio", TWILIO_ACCOUNT_SID="AC1", TWILIO_AUTH_TOKEN="x",
                       TWILIO_SMS_FROM="+1555")
    def test_twilio_failure_recorded(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"{}"))
        with mock.patch.object(channels, "_http", side_effect=err):
            note = notify(member=self.member, title="Reminder", external=True)
        self.assertEqual(note.deliveries.get().status, "failed")

    @override_settings(NOTIFY_CHANNELS=["sms"], SMS_PROVIDER="notifylk", NOTIFYLK_USER_ID="1", NOTIFYLK_API_KEY="k")
    def test_notifylk_accepted_is_sent_not_delivered(self):
        with mock.patch.object(channels, "_http", return_value={"status": "success"}):
            note = notify(member=self.member, title="Reminder", external=True)
        d = note.deliveries.get()
        self.assertEqual(d.status, "sent")
        self.assertIn("no delivery receipts", d.detail)

    def test_notification_centre_and_delivery_log_permissions(self):
        notify(user=self.login, title="Mine", category="payment")
        self.client.force_login(self.login)
        response = self.client.get(reverse("notifications:list"), {"category": "payment"})
        self.assertEqual(len(response.context["notifications"]), 1)
        self.assertEqual(self.client.get(reverse("notifications:deliveries")).status_code, 403)
        self.client.force_login(make_user(Role.GYM_MANAGER))
        self.assertEqual(self.client.get(reverse("notifications:deliveries")).status_code, 200)


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------
class AnnouncementTests(TestCase):
    def setUp(self):
        self.manager = make_user(Role.GYM_MANAGER)
        self.trainer = make_user(Role.TRAINER)
        self.login, self.member = make_member_with_login("annmember")

    def test_publish_notifies_audience_once(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("announcements:create"), {
            "title": "Closed on Poya day", "kind": "closure", "body": "The gym is closed on Friday.",
            "audience": "everyone", "publish_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "notify_recipients": "on", "is_active": "on"})
        a = Announcement.objects.get()
        self.assertIsNotNone(a.notified_at)
        self.assertTrue(Notification.objects.filter(user=self.login, category="announcement").exists())
        self.assertTrue(Notification.objects.filter(user=self.trainer, category="announcement").exists())
        before = Notification.objects.count()
        self.assertEqual(announce.publish_if_due(a), 0)
        self.assertEqual(announce.publish_due(), 0)
        self.assertEqual(Notification.objects.count(), before)

    def test_audience_targeting_and_visibility(self):
        staff_only = Announcement.objects.create(title="Staff meeting", body="Mon 8am", audience="staff",
                                                 notify_recipients=False)
        trainers = Announcement.objects.create(title="Trainer CPD", body="x", audience="role", role="trainer",
                                               notify_recipients=False)
        members = Announcement.objects.create(title="New classes", body="x", audience="members", notify_recipients=False)
        self.assertEqual(announce.visible_to(self.login), [members])
        self.assertCountEqual(announce.visible_to(self.trainer), [staff_only, trainers])
        self.client.force_login(self.login)
        self.assertEqual(self.client.get(staff_only.get_absolute_url()).status_code, 403)
        self.assertEqual(self.client.get(members.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(reverse("announcements:manage")).status_code, 403)

    def test_scheduled_and_expired(self):
        later = Announcement.objects.create(title="Later", body="x", audience="everyone",
                                            publish_at=timezone.now() + timedelta(hours=2))
        self.assertEqual(announce.publish_due(), 0)
        self.assertEqual(announce.visible_to(self.login), [])
        Announcement.objects.filter(pk=later.pk).update(publish_at=timezone.now() - timedelta(minutes=1))
        self.assertGreater(announce.publish_due(), 0)
        Announcement.objects.filter(pk=later.pk).update(expires_at=timezone.now())
        self.assertEqual(announce.visible_to(self.login), [])

    def test_plan_audience(self):
        plan = make_plan(name="Gold")
        make_membership(self.member, plan, TODAY())
        other_login, _ = make_member_with_login("silvermember")
        a = Announcement.objects.create(title="Gold perks", body="x", audience="plan", plan=plan)
        announce.publish_if_due(a)
        self.assertTrue(Notification.objects.filter(user=self.login, title="Gold perks").exists())
        self.assertFalse(Notification.objects.filter(user=other_login, title="Gold perks").exists())


# ---------------------------------------------------------------------------
# Reports & financial rules
# ---------------------------------------------------------------------------
class ReportTests(TestCase):
    def setUp(self):
        self.admin = make_user(Role.SUPER_ADMIN)
        self.desk = make_user(Role.RECEPTIONIST)
        plan = make_plan(price="6000")
        self.m1 = make_membership(make_member(joined_on=TODAY() - timedelta(days=60)), plan, TODAY() - timedelta(days=2))
        self.m2 = make_membership(make_member(joined_on=TODAY() - timedelta(days=60)), plan, TODAY() - timedelta(days=2))
        p1, _ = pay.record_payment(self.m1, D("6000"), "cash", TODAY(), self.desk)
        pay.record_payment(self.m2, D("2000"), "card", TODAY(), self.desk)
        pay.record_payment(self.m2, D("1000"), "bank_transfer", TODAY(), self.desk, pending=True)  # not collected
        pay.refund_payment(p1, D("500"), "cash", TODAY(), self.admin, "goodwill")
        expense("30000", "rent", by=self.admin)
        expense("100000", "equipment", by=self.admin)              # capital
        expense("999", "rent", status="voided", by=self.admin)      # excluded

    def test_financial_rules(self):
        s = finance.summary(TODAY(), TODAY())
        self.assertEqual((s["collected"]["gross"], s["collected"]["refunds"], s["collected"]["net"]),
                         (D("8000"), D("500"), D("7500")))
        self.assertEqual((s["expenses"]["operating"], s["expenses"]["capital"]), (D("30000"), D("100000")))
        self.assertEqual(s["operating_profit"], D("-22500"))
        self.assertEqual(s["after_capital"], D("-122500"))
        self.assertEqual(finance.invoiced(TODAY() - timedelta(days=5), TODAY())["memberships"], D("12000"))
        self.assertEqual(finance.outstanding_now()["total"], D("4500"))  # 4000 on m2 (pending ignored) + 500 refunded on m1

    def test_all_reports_render_for_admin(self):
        self.client.force_login(self.admin)
        from apps.reports.definitions import REGISTRY
        index = self.client.get(reverse("reports:index"))
        self.assertEqual(sum(len(v) for v in index.context["sections"].values()), len(REGISTRY))
        for slug in REGISTRY:
            url = reverse("reports:report", args=[slug])
            self.assertEqual(self.client.get(url).status_code, 200, slug)
            self.assertEqual(self.client.get(url, {"print": "1"}).status_code, 200, slug)
            csv_resp = self.client.get(url, {"export": "csv"})
            self.assertEqual(csv_resp["Content-Type"], "text/csv; charset=utf-8", slug)
            pdf = self.client.get(url, {"export": "pdf"})
            self.assertTrue(pdf.content.startswith(b"%PDF"), slug)

    def test_payments_report_figures_and_csv(self):
        self.client.force_login(self.admin)
        url = reverse("reports:report", args=["payments"])
        response = self.client.get(url, {"date_from": TODAY().isoformat(), "date_to": TODAY().isoformat()})
        summary = dict(response.context["summary"])
        self.assertEqual(summary["Net collected"], "LKR 7,500.00")
        csv_text = self.client.get(url, {"date_from": TODAY().isoformat(), "date_to": TODAY().isoformat(),
                                         "export": "csv"}).content.decode("utf-8-sig")
        self.assertIn("Net collected", csv_text.splitlines()[0])
        self.assertIn("7500.00", csv_text)

    def test_csv_formula_injection_neutralised(self):
        from apps.members.models import Member
        Member.objects.create(full_name="=HYPERLINK(\"http://evil\")", phone="+94770009999", joined_on=TODAY())
        self.client.force_login(self.admin)
        csv_text = self.client.get(reverse("reports:report", args=["member-registrations"]), {"export": "csv"}).content.decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", csv_text)

    def test_report_permissions(self):
        self.client.force_login(make_user(Role.ACCOUNTANT))  # finance, but no attendance/equipment-manage
        self.assertEqual(self.client.get(reverse("reports:report", args=["revenue-vs-expenses"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("reports:report", args=["attendance"])).status_code, 403)
        self.client.force_login(make_user(Role.GYM_MANAGER))  # can view expenses but has view_payments too
        self.assertEqual(self.client.get(reverse("reports:report", args=["expenses"])).status_code, 200)
        for role in (Role.RECEPTIONIST, Role.TRAINER, Role.MEMBER):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(reverse("reports:index")).status_code, 403, role)
            self.assertEqual(self.client.get(reverse("reports:report", args=["payments"])).status_code, 403, role)
        self.assertEqual(self.client.get(reverse("reports:report", args=["no-such-report"])).status_code, 404)

    def test_dashboard_financial_and_ops_stats(self):
        InventoryItem.objects.create(sku="LOW", name="Towels", category="towels", minimum_stock=D("5"))
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard:home"))
        stats = {s["slot"].key: s["value"] for s in response.context["stats"]}
        self.assertEqual(stats["expenses_month"], D("30000"))
        self.assertEqual(stats["profit_month"], finance.summary(TODAY().replace(day=1), TODAY())["operating_profit"])
        self.assertEqual(stats["low_stock"], 1)
        self.assertIn("revenue_vs_expenses", response.context["panels"])
        self.client.force_login(self.desk)
        stats = {s["slot"].key for s in self.client.get(reverse("dashboard:home")).context["stats"]}
        self.assertNotIn("expenses_month", stats)
        self.assertIn("low_stock", stats)


