from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.memberships import services
from apps.memberships.models import MembershipPlan

from .factories import make_member, make_plan


def plan_payload(**overrides):
    data = {
        "name": "Gold Monthly", "description": "Full access", "duration_preset": "monthly",
        "duration_value": "", "duration_unit": "", "price": "7500.00",
        "benefits": "Gym floor\nGroup classes", "access_restrictions": "", "is_active": "on",
    }
    data.update(overrides)
    return data


class PlanCreateTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(Role.GYM_MANAGER))
        self.url = reverse("memberships:plan_create")

    def test_create_standard_plans(self):
        for preset, value in (("monthly", 1), ("quarterly", 3), ("half_year", 6), ("annual", 12)):
            response = self.client.post(self.url, plan_payload(name=f"P {preset}", duration_preset=preset))
            plan = MembershipPlan.objects.get(name=f"P {preset}")
            self.assertRedirects(response, plan.get_absolute_url())
            self.assertEqual((plan.duration_value, plan.duration_unit), (value, "month"))
        self.assertEqual(MembershipPlan.objects.get(name="P annual").duration_display, "1 year")

    def test_create_custom_duration(self):
        self.client.post(self.url, plan_payload(name="Two Weeks", duration_preset="custom",
                                                duration_value="14", duration_unit="day", price="2000"))
        plan = MembershipPlan.objects.get(name="Two Weeks")
        self.assertEqual((plan.duration_value, plan.duration_unit), (14, "day"))
        self.assertEqual(plan.benefit_list, ["Gym floor", "Group classes"])

    def test_invalid_price_and_duration(self):
        for data, field in [
            (plan_payload(price="-1"), "price"),
            (plan_payload(price="abc"), "price"),
            (plan_payload(price="99999999"), "price"),
            (plan_payload(duration_preset="custom", duration_value="", duration_unit="month"), "duration_value"),
            (plan_payload(duration_preset="custom", duration_value="0", duration_unit="month"), "duration_value"),
            (plan_payload(duration_preset="custom", duration_value="61", duration_unit="month"), "duration_value"),
            (plan_payload(duration_preset="custom", duration_value="2000", duration_unit="day"), "duration_value"),
            (plan_payload(duration_preset="weekly"), "duration_preset"),
        ]:
            response = self.client.post(self.url, data)
            self.assertEqual(response.status_code, 200, data)
            self.assertIn(field, response.context["form"].errors, data)
        self.assertFalse(MembershipPlan.objects.exists())

    def test_duplicate_name_case_insensitive(self):
        make_plan(name="Gold Monthly")
        response = self.client.post(self.url, plan_payload(name="gold   MONTHLY"))
        self.assertContains(response, "already exists")

    def test_database_constraints(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_plan(price="-5")
        with self.assertRaises(IntegrityError), transaction.atomic():
            MembershipPlan.objects.create(name="Bad", duration_value=0, duration_unit="month", price=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MembershipPlan.objects.create(name="Bad2", duration_value=100, duration_unit="month", price=1)


class PlanPricePermissionTests(TestCase):
    def setUp(self):
        self.plan = make_plan(name="Silver", price="5000.00")
        self.clerk = make_user(Role.RECEPTIONIST)
        # Grant plan editing but NOT price changes to this receptionist.
        from django.contrib.auth.models import Permission
        self.clerk.user_permissions.add(Permission.objects.get(codename="manage_plans"))
        self.client.force_login(self.clerk)

    def test_cannot_change_price_with_direct_post(self):
        url = reverse("memberships:plan_update", args=[self.plan.pk])
        response = self.client.get(url)
        self.assertTrue(response.context["form"].fields["price"].disabled)
        response = self.client.post(url, plan_payload(name="Silver Plus", price="1.00"))
        self.assertRedirects(response, self.plan.get_absolute_url())
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.name, "Silver Plus")  # other edits allowed
        self.assertEqual(self.plan.price, Decimal("5000.00"))  # price untouched

    def test_cannot_create_plans_without_price_permission(self):
        response = self.client.post(reverse("memberships:plan_create"), plan_payload())
        self.assertEqual(response.status_code, 403)

    def test_manager_can_change_price(self):
        self.client.force_login(make_user(Role.GYM_MANAGER))
        self.client.post(reverse("memberships:plan_update", args=[self.plan.pk]), plan_payload(name="Silver", price="5500"))
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price, Decimal("5500.00"))

    def test_roles_without_plan_permission_get_403(self):
        for role in (Role.RECEPTIONIST, Role.TRAINER, Role.ACCOUNTANT, Role.MEMBER):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(reverse("memberships:plan_update", args=[self.plan.pk])).status_code, 403)
            self.assertEqual(
                self.client.post(reverse("memberships:plan_toggle_active", args=[self.plan.pk])).status_code, 403
            )
        self.plan.refresh_from_db()
        self.assertTrue(self.plan.is_active)


class PlanHistoryTests(TestCase):
    def test_editing_plan_keeps_existing_agreements(self):
        manager = make_user(Role.GYM_MANAGER)
        plan = make_plan(name="Basic", price="4000.00", months=1)
        membership = services.assign_membership(make_member(), plan, timezone.localdate(), manager)
        self.client.force_login(manager)
        self.client.post(reverse("memberships:plan_update", args=[plan.pk]),
                         plan_payload(name="Basic Plus", duration_preset="quarterly", price="9000"))
        membership.refresh_from_db()
        self.assertEqual(membership.plan_name, "Basic")
        self.assertEqual(membership.price, Decimal("4000.00"))
        self.assertEqual(membership.duration_value, 1)
        response = self.client.get(membership.get_absolute_url())
        self.assertContains(response, "keeps its original terms")

    def test_deactivate_plan_keeps_memberships_and_blocks_new(self):
        manager = make_user(Role.GYM_MANAGER)
        plan = make_plan(name="Legacy")
        member = make_member()
        services.assign_membership(member, plan, timezone.localdate(), manager)
        self.client.force_login(manager)
        self.client.post(reverse("memberships:plan_toggle_active", args=[plan.pk]))
        plan.refresh_from_db()
        self.assertFalse(plan.is_active)
        self.assertEqual(plan.memberships.count(), 1)
        with self.assertRaisesMessage(services.MembershipError, "inactive"):
            services.assign_membership(make_member(), plan, timezone.localdate(), manager)
        # Inactive plans aren't offered in the assignment form either.
        response = self.client.get(reverse("memberships:assign", args=[make_member().pk]))
        self.assertNotIn(plan, response.context["form"].fields["plan"].queryset)

    def test_plan_list_and_detail(self):
        make_plan(name="Visible Plan")
        make_plan(name="Old Plan", is_active=False)
        self.client.force_login(make_user(Role.ACCOUNTANT))  # view-only role
        response = self.client.get(reverse("memberships:plan_list"))
        self.assertContains(response, "Visible Plan")
        self.assertFalse(response.context["can_create"])
        response = self.client.get(reverse("memberships:plan_list"), {"status": "inactive"})
        self.assertNotContains(response, "Visible Plan")
        self.assertContains(response, "Old Plan")
