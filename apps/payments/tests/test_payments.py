import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.core.models import ActivityLog
from apps.memberships import services as membership_services
from apps.memberships.models import Membership
from apps.memberships.tests.factories import make_member, make_membership, make_plan
from apps.payments import services
from apps.payments.balances import PaymentStatus, balance_for, with_balances
from apps.payments.models import BalanceAdjustment, Payment

D = Decimal
TODAY = timezone.localdate


class PaymentBase(TestCase):
    def setUp(self):
        self.today = TODAY()
        self.staff = make_user(Role.RECEPTIONIST)
        self.manager = make_user(Role.GYM_MANAGER)
        self.member = make_member(joined_on=self.today - timedelta(days=100))
        self.plan = make_plan(name="Monthly", price="6000.00")
        self.membership = make_membership(self.member, self.plan, self.today - timedelta(days=3))

    def pay(self, amount, method="cash", **kw):
        payment, _ = services.record_payment(self.membership, D(amount), method, self.today, self.staff, **kw)
        return payment


class BalanceTests(PaymentBase):
    def test_unpaid_partial_paid(self):
        b = balance_for(self.membership)
        self.assertEqual((b.charge, b.outstanding, b.status), (D("6000.00"), D("6000.00"), PaymentStatus.UNPAID))
        self.pay("2500")
        b = balance_for(self.membership)
        self.assertEqual((b.net_paid, b.outstanding, b.status), (D("2500"), D("3500"), PaymentStatus.PARTIALLY_PAID))
        self.pay("3500")
        b = balance_for(self.membership)
        self.assertEqual((b.outstanding, b.status), (D("0"), PaymentStatus.PAID))

    def test_pending_does_not_count_as_collected(self):
        self.pay("6000", method="bank_transfer", pending=True)
        b = balance_for(self.membership)
        self.assertEqual(b.status, PaymentStatus.PENDING)
        self.assertEqual(b.outstanding, D("6000"))
        self.assertEqual(b.payable, D("0"))  # can't pay twice while one is pending

    def test_refund_statuses(self):
        p = self.pay("6000")
        services.refund_payment(p, D("1000"), "cash", self.today, self.manager, "Goodwill")
        self.assertEqual(balance_for(self.membership).status, PaymentStatus.PARTIALLY_REFUNDED)
        services.refund_payment(p, D("5000"), "cash", self.today, self.manager, "Rest")
        b = balance_for(self.membership)
        self.assertEqual(b.status, PaymentStatus.REFUNDED)
        self.assertEqual(b.net_paid, D("0"))

    def test_cancelled_membership_charge_stops(self):
        self.pay("2000")
        membership_services.cancel_membership(self.membership, self.manager, "Moving")
        self.membership.refresh_from_db()
        b = balance_for(self.membership)
        self.assertEqual((b.charge, b.outstanding), (D("2000"), D("0")))
        unpaid = make_membership(make_member(), self.plan, self.today)
        membership_services.cancel_membership(unpaid, self.manager, "Wrong plan")
        unpaid.refresh_from_db()
        self.assertEqual(balance_for(unpaid).status, PaymentStatus.NO_CHARGE)

    def test_db_annotation_matches_python(self):
        self.pay("1500")
        services.adjust_balance(self.membership, "discount", D("500"), "Promo", self.manager)
        services.adjust_balance(self.membership, "surcharge", D("200"), "Locker", self.manager)
        annotated = with_balances(Membership.objects.filter(pk=self.membership.pk)).get()
        self.assertEqual(annotated.outstanding, balance_for(self.membership).outstanding)
        self.assertEqual(annotated.outstanding, D("4200.00"))  # 6000 - 500 + 200 - 1500


class RecordPaymentTests(PaymentBase):
    def test_full_payment_gets_numbers_and_audit(self):
        p = self.pay("6000", method="card", reference="AUTH 123456")
        self.assertTrue(p.reference.startswith("TXN-"))
        self.assertTrue(p.receipt_number.startswith("RCPT-"))
        self.assertEqual((p.member, p.membership, p.recorded_by), (self.member, self.membership, self.staff))
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.PAYMENT_RECORDED).exists())

    def test_unique_references(self):
        refs = {self.pay("100").reference for _ in range(5)}
        self.assertEqual(len(refs), 5)

    def test_overpayment_rejected(self):
        self.pay("5000")
        with self.assertRaisesMessage(services.PaymentError, "exceeds the payable balance of LKR 1,000.00"):
            self.pay("1000.01")
        self.pay("1000")
        with self.assertRaisesMessage(services.PaymentError, "Nothing is payable"):
            self.pay("1")

    def test_invalid_amounts_dates_methods(self):
        for amount in ("0", "-5", "10.005"):
            with self.assertRaises(services.PaymentError):
                self.pay(amount)
        with self.assertRaisesMessage(services.PaymentError, "future"):
            services.record_payment(self.membership, D("10"), "cash", self.today + timedelta(days=1), self.staff)
        with self.assertRaisesMessage(services.PaymentError, "days ago"):
            services.record_payment(self.membership, D("10"), "cash", self.today - timedelta(days=400), self.staff)
        with self.assertRaisesMessage(services.PaymentError, "valid payment method"):
            self.pay("10", method="crypto")
        with self.assertRaisesMessage(services.PaymentError, "Only bank transfers"):
            self.pay("10", method="cash", pending=True)

    def test_card_numbers_are_never_stored(self):
        with self.assertRaisesMessage(services.PaymentError, "card numbers"):
            self.pay("100", method="card", reference="4111 1111 1111 1111")
        with self.assertRaisesMessage(services.PaymentError, "card numbers"):
            self.pay("100", method="card", notes="paid with 5500-0000-0000-0004")
        self.pay("100", method="card", reference="Visa ending 1111, auth 834221")  # allowed
        self.assertFalse(Payment.objects.filter(external_reference__contains="4111 1111").exists())

    def test_duplicate_submission_records_once(self):
        token = uuid.uuid4()
        first, created1 = services.record_payment(self.membership, D("1000"), "cash", self.today, self.staff, token=token)
        second, created2 = services.record_payment(self.membership, D("1000"), "cash", self.today, self.staff, token=token)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(first, second)
        self.assertEqual(Payment.objects.count(), 1)

    def test_concurrent_duplicate_resolved_by_unique_token(self):
        token = uuid.uuid4()
        winner, _ = services.record_payment(self.membership, D("500"), "cash", self.today, self.staff, token=token)
        # Simulate the second request passing the pre-check before the first committed.
        with mock.patch("apps.payments.services._existing_for_token", side_effect=[None, winner]):
            again, created = services.record_payment(self.membership, D("500"), "cash", self.today, self.staff, token=token)
        self.assertFalse(created)
        self.assertEqual(again, winner)
        self.assertEqual(Payment.objects.count(), 1)

    def test_rollback_on_failure(self):
        with mock.patch.object(Payment, "assign_numbers", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.pay("100")
        self.assertFalse(Payment.objects.exists())

    def test_pending_then_confirm(self):
        p = self.pay("6000", method="bank_transfer", pending=True, reference="BOC-7781")
        self.assertIsNone(p.receipt_number)
        services.confirm_payment(p, self.staff)
        p.refresh_from_db()
        self.assertEqual(p.status, Payment.Status.COMPLETED)
        self.assertTrue(p.receipt_number)
        self.assertEqual(balance_for(self.membership).status, PaymentStatus.PAID)
        with self.assertRaisesMessage(services.PaymentError, "Only pending"):
            services.confirm_payment(p, self.staff)

    def test_payment_on_cancelled_membership_rejected(self):
        membership_services.cancel_membership(self.membership, self.manager, "Left")
        self.membership.refresh_from_db()
        with self.assertRaisesMessage(services.PaymentError, "Nothing is payable"):
            self.pay("100")

    def test_database_constraints(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(member=self.member, membership=self.membership, amount=D("-1"), method="cash",
                                   paid_on=self.today, recorded_by=self.staff)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(member=self.member, membership=self.membership, kind="refund", amount=D("1"),
                                   method="cash", paid_on=self.today, recorded_by=self.staff)  # no refund_of


class RefundVoidAdjustTests(PaymentBase):
    def test_refund_limits(self):
        p = self.pay("3000")
        refund, _ = services.refund_payment(p, D("1000"), "cash", self.today, self.manager, "Partial")
        self.assertTrue(refund.receipt_number.startswith("RFD-"))
        self.assertEqual(refund.refund_of, p)
        with self.assertRaisesMessage(services.PaymentError, "2,000.00"):
            services.refund_payment(p, D("2000.01"), "cash", self.today, self.manager, "Too much")
        with self.assertRaisesMessage(services.PaymentError, "reason"):
            services.refund_payment(p, D("1"), "cash", self.today, self.manager, "  ")
        with self.assertRaisesMessage(services.PaymentError, "Only completed"):
            services.refund_payment(refund, D("1"), "cash", self.today, self.manager, "Refund a refund")

    def test_void_keeps_record_and_restores_balance(self):
        p = self.pay("6000")
        services.void_payment(p, self.manager, "Wrong member")
        p.refresh_from_db()
        self.assertEqual(p.status, Payment.Status.VOIDED)
        self.assertEqual(p.voided_by, self.manager)
        self.assertEqual(balance_for(self.membership).outstanding, D("6000"))
        self.assertTrue(Payment.objects.filter(pk=p.pk).exists())
        with self.assertRaisesMessage(services.PaymentError, "already void"):
            services.void_payment(p, self.manager, "Again")

    def test_cannot_void_payment_with_refunds(self):
        p = self.pay("6000")
        refund, _ = services.refund_payment(p, D("100"), "cash", self.today, self.manager, "x")
        with self.assertRaisesMessage(services.PaymentError, "Void the refunds first"):
            services.void_payment(p, self.manager, "Oops")
        services.void_payment(refund, self.manager, "Refund entered twice")
        services.void_payment(p, self.manager, "Now allowed")

    def test_adjustments(self):
        self.pay("4000")
        with self.assertRaisesMessage(services.PaymentError, "can't exceed the unpaid balance"):
            services.adjust_balance(self.membership, "discount", D("2000.01"), "Too big", self.manager)
        services.adjust_balance(self.membership, "discount", D("2000"), "Loyalty", self.manager)
        self.assertEqual(balance_for(self.membership).status, PaymentStatus.PAID)
        services.adjust_balance(self.membership, "surcharge", D("500"), "Locker rental", self.manager)
        self.assertEqual(balance_for(self.membership).outstanding, D("500"))
        self.assertEqual(BalanceAdjustment.objects.count(), 2)
        with self.assertRaisesMessage(services.PaymentError, "reason"):
            services.adjust_balance(self.membership, "discount", D("1"), "", self.manager)


class PaymentViewTests(PaymentBase):
    def record_url(self):
        return reverse("payments:record", args=[self.membership.pk])

    def post_payment(self, amount="2000", **extra):
        data = {"amount": amount, "method": "cash", "paid_on": self.today.isoformat(),
                "external_reference": "", "notes": "", "submission_token": str(uuid.uuid4())}
        data.update(extra)
        return self.client.post(self.record_url(), data)

    def test_record_via_form_and_resubmit(self):
        self.client.force_login(self.staff)
        token = str(uuid.uuid4())
        response = self.post_payment(submission_token=token)
        p = Payment.objects.get()
        self.assertRedirects(response, p.get_absolute_url())
        response = self.post_payment(submission_token=token)  # browser resubmits the same form
        self.assertRedirects(response, p.get_absolute_url())
        self.assertEqual(Payment.objects.count(), 1)

    def test_form_errors_shown(self):
        self.client.force_login(self.staff)
        response = self.post_payment(amount="99999")
        self.assertContains(response, "exceeds the payable balance")
        response = self.post_payment(method="cash", pending="on")
        self.assertIn("pending", response.context["form"].errors)
        self.assertFalse(Payment.objects.exists())

    def test_role_permissions(self):
        p = self.pay("3000")
        # Receptionist and accountant record payments but can't refund/void/adjust.
        for role in (Role.RECEPTIONIST, Role.ACCOUNTANT):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(self.record_url()).status_code, 200, role)
            self.assertEqual(self.client.get(reverse("payments:refund", args=[p.pk])).status_code, 403, role)
            self.assertEqual(self.client.post(reverse("payments:void", args=[p.pk]), {"reason": "abc"}).status_code, 403)
            self.assertEqual(self.client.post(reverse("payments:adjust", args=[self.membership.pk]),
                                              {"kind": "discount", "amount": "100", "reason": "abc"}).status_code, 403)
        # Trainer and member have no payment access at all.
        for role in (Role.TRAINER, Role.MEMBER):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(reverse("payments:list")).status_code, 403, role)
            self.assertEqual(self.post_payment().status_code, 403, role)
        p.refresh_from_db()
        self.assertEqual(p.status, Payment.Status.COMPLETED)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertFalse(BalanceAdjustment.objects.exists())

    def test_manager_refund_and_void_via_views(self):
        p = self.pay("3000")
        self.client.force_login(self.manager)
        response = self.client.post(reverse("payments:refund", args=[p.pk]), {
            "amount": "500", "method": "cash", "paid_on": self.today.isoformat(), "reason": "Unused classes",
            "submission_token": str(uuid.uuid4()),
        })
        refund = Payment.objects.get(kind="refund")
        self.assertRedirects(response, refund.get_absolute_url())
        self.client.post(reverse("payments:void", args=[refund.pk]), {"reason": "Entered by mistake"})
        refund.refresh_from_db()
        self.assertEqual(refund.status, Payment.Status.VOIDED)

    def test_pages_render(self):
        p = self.pay("3000", method="card", reference="auth 7781")
        self.client.force_login(self.manager)
        for url in (reverse("payments:list"), reverse("payments:outstanding"), p.get_absolute_url(),
                    reverse("payments:refund", args=[p.pk]), reverse("payments:adjust", args=[self.membership.pk]),
                    reverse("payments:receipt", args=[p.pk]), self.membership.get_absolute_url(),
                    self.member.get_absolute_url(), reverse("dashboard:home")):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get(self.record_url()).status_code, 200)  # managers may record payments
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.record_url()).status_code, 200)

    def test_list_filters_and_totals(self):
        self.pay("1000", method="cash")
        p2 = self.pay("2000", method="card")
        services.refund_payment(p2, D("300"), "card", self.today, self.manager, "x")
        self.client.force_login(self.manager)
        response = self.client.get(reverse("payments:list"), {"method": "card"})
        self.assertEqual(len(response.context["payments"]), 2)
        totals = response.context["totals"]
        self.assertEqual((totals["received"], totals["refunded"], totals["net"]), (D("2000"), D("300"), D("1700")))
        response = self.client.get(reverse("payments:list"), {"q": p2.reference})
        self.assertEqual(len(response.context["payments"]), 1)

    def test_outstanding_page_excludes_paid_and_cancelled(self):
        other = make_membership(make_member(full_name="Fully Paid"), self.plan, self.today)
        services.record_payment(other, D("6000"), "cash", self.today, self.staff)
        cancelled = make_membership(make_member(full_name="Cancelled Guy"), self.plan, self.today)
        membership_services.cancel_membership(cancelled, self.manager, "Left")
        self.client.force_login(self.manager)
        response = self.client.get(reverse("payments:outstanding"))
        self.assertContains(response, self.member.full_name)
        self.assertNotContains(response, "Fully Paid")
        self.assertNotContains(response, "Cancelled Guy")
        self.assertEqual(response.context["total"], D("6000"))


class ReceiptTests(PaymentBase):
    def test_receipt_shows_required_fields(self):
        self.pay("2000")
        p = self.pay("1500", method="card", reference="AUTH 99")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("payments:receipt", args=[p.pk]))
        for text in (p.receipt_number, p.reference, self.member.full_name, self.member.member_id, "Monthly",
                     "LKR 6,000.00", "LKR 1,500.00", "LKR 3,500.00", "LKR 2,500.00", "Card", "AUTH 99"):
            self.assertContains(response, text)

    def test_receipt_balance_is_as_at_the_transaction(self):
        first = self.pay("1000")
        self.pay("5000")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("payments:receipt", args=[first.pk]))
        self.assertEqual(response.context["outstanding"], D("5000"))  # later payment not included

    def test_pdf_download(self):
        p = self.pay("2000")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("payments:receipt_pdf", args=[p.pk]))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(p.receipt_number, response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_pdf_handles_non_latin_names(self):
        self.member.full_name = "කසුන් පෙරේරා Perera"
        self.member.save()
        p = self.pay("2000")
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("payments:receipt_pdf", args=[p.pk])).status_code, 200)

    def test_receipt_access_control(self):
        p = self.pay("2000")
        owner = make_user(Role.MEMBER, username="receiptowner")
        owner.member_profile.delete()  # drop the auto-created profile; link the paying member instead
        self.member.user = owner
        self.member.save()
        self.client.force_login(owner)  # the member themself
        self.assertEqual(self.client.get(reverse("payments:receipt", args=[p.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("payments:receipt_pdf", args=[p.pk])).status_code, 200)
        self.client.force_login(make_user(Role.MEMBER, username="nosy"))  # another member
        self.assertEqual(self.client.get(reverse("payments:receipt", args=[p.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("payments:receipt_pdf", args=[p.pk])).status_code, 403)
        self.client.force_login(make_user(Role.TRAINER))
        self.assertEqual(self.client.get(reverse("payments:receipt", args=[p.pk])).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("payments:receipt", args=[p.pk])).status_code, 302)

    def test_no_receipt_for_pending(self):
        p = self.pay("2000", method="bank_transfer", pending=True)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("payments:receipt", args=[p.pk])).status_code, 404)

    def test_void_receipt_is_marked(self):
        p = self.pay("2000")
        services.void_payment(p, self.manager, "Mistake")
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("payments:receipt", args=[p.pk])), "was voided")


class FinancialDashboardTests(PaymentBase):
    def test_collected_vs_outstanding(self):
        p = self.pay("4000")
        services.refund_payment(p, D("500"), "cash", self.today, self.manager, "x")
        services.record_payment(self.membership, D("1000"), "bank_transfer", self.today, self.staff, pending=True)
        old = make_membership(make_member(), self.plan, self.today - timedelta(days=20))
        services.record_payment(old, D("6000"), "cash", self.today - timedelta(days=40), self.staff)
        void = make_membership(make_member(), self.plan, self.today)
        vp, _ = services.record_payment(void, D("6000"), "cash", self.today, self.staff)
        services.void_payment(vp, self.manager, "Wrong")

        self.client.force_login(self.manager)
        response = self.client.get(reverse("dashboard:home"))
        stats = {s["slot"].key: s for s in response.context["stats"]}
        self.assertEqual(stats["today_revenue"]["value"], D("3500"))  # 4000 - 500; pending & void excluded
        month_start = self.today.replace(day=1)
        expected_month = D("3500") + (D("6000") if self.today - timedelta(days=40) >= month_start else 0)
        self.assertEqual(stats["monthly_revenue"]["value"], expected_month)
        # Outstanding: 6000-3500 on first (pending not collected) + 6000 on voided one.
        self.assertEqual(stats["outstanding_payments"]["value"], D("2500") + D("6000"))
        panels = response.context["panels"]
        self.assertNotIn(vp, panels["recent_payments"]["value"])
        breakdown = {r["key"]: r["count"] for r in panels["payment_breakdown"]["value"]["rows"]}
        self.assertEqual(breakdown["paid"], 1)
        self.assertEqual(breakdown["unpaid"], 1)
        self.assertEqual(breakdown["partially_refunded"], 1)

    def test_finance_widgets_hidden_from_trainer(self):
        self.client.force_login(make_user(Role.TRAINER))
        stats = {s["slot"].key for s in self.client.get(reverse("dashboard:home")).context["stats"]}
        self.assertFalse({"today_revenue", "monthly_revenue", "outstanding_payments"} & stats)
