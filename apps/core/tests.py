from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.core.templatetags.ui_tags import money


class TemplateHelperTests(TestCase):
    @override_settings(CURRENCY_SYMBOL="$")
    def test_money_filter(self):
        self.assertEqual(money(1234.5), "$1,234.50")
        self.assertEqual(money(0), "$0.00")
        self.assertEqual(money(None), "—")


class NavigationTests(TestCase):
    def test_sidebar_reflects_role_permissions(self):
        self.client.force_login(make_user(Role.SUPER_ADMIN))
        nav = self.client.get(reverse("dashboard:home")).context["nav_sections"]
        labels = {item["label"] for section in nav for item in section["items"]}
        self.assertTrue({"Users", "Roles & Permissions", "Activity Log", "Payments"} <= labels)

        self.client.force_login(make_user(Role.TRAINER))
        nav = self.client.get(reverse("dashboard:home")).context["nav_sections"]
        labels = {item["label"] for section in nav for item in section["items"]}
        self.assertNotIn("Users", labels)
        self.assertNotIn("Payments", labels)
        self.assertTrue({"Trainers", "Appointments", "Workout Plans", "Exercise Library"} <= labels)


@override_settings(DEBUG=False)
class ErrorPageTests(TestCase):
    def test_404_page_is_friendly(self):
        self.client.force_login(make_user(Role.MEMBER))
        response = self.client.get("/definitely-not-a-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)

    def test_403_page_is_friendly(self):
        self.client.force_login(make_user(Role.MEMBER))
        response = self.client.get(reverse("accounts:user_list"))
        self.assertContains(response, "Access denied", status_code=403)
