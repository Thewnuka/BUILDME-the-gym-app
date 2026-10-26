from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import Member
from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.core.utils import log_activity
from apps.dashboard import registry


def stat_map(response):
    return {s["slot"].key: s for s in response.context["stats"]}


class DashboardAccessTests(TestCase):
    def test_anonymous_redirected(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_staff_roles_get_admin_dashboard(self):
        for role in (Role.SUPER_ADMIN, Role.GYM_MANAGER, Role.RECEPTIONIST, Role.TRAINER, Role.ACCOUNTANT):
            self.client.force_login(make_user(role))
            response = self.client.get(reverse("dashboard:home"))
            self.assertEqual(response.status_code, 200, role)
            self.assertTemplateUsed(response, "dashboard/admin.html")

    def test_member_gets_member_dashboard(self):
        self.client.force_login(make_user(Role.MEMBER))
        response = self.client.get(reverse("dashboard:home"))
        self.assertTemplateUsed(response, "dashboard/member.html")
        self.assertNotIn("stats", response.context)


class DashboardWidgetPermissionTests(TestCase):
    def test_trainer_does_not_see_financial_widgets(self):
        self.client.force_login(make_user(Role.TRAINER))
        stats = stat_map(self.client.get(reverse("dashboard:home")))
        self.assertIn("total_members", stats)
        self.assertNotIn("today_revenue", stats)
        self.assertNotIn("monthly_revenue", stats)
        self.assertNotIn("outstanding_payments", stats)

    def test_accountant_sees_financial_widgets(self):
        self.client.force_login(make_user(Role.ACCOUNTANT))
        stats = stat_map(self.client.get(reverse("dashboard:home")))
        self.assertIn("monthly_revenue", stats)

    def test_super_admin_sees_every_widget(self):
        self.client.force_login(make_user(Role.SUPER_ADMIN))
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(len(response.context["stats"]), len(registry.STAT_SLOTS))
        self.assertEqual(set(response.context["panels"]), set(registry.PANEL_SLOTS))

    def test_recent_activity_only_with_permission(self):
        self.client.force_login(make_user(Role.TRAINER))
        self.assertNotIn("recent_activity", self.client.get(reverse("dashboard:home")).context["panels"])
        self.client.force_login(make_user(Role.GYM_MANAGER))
        self.assertIn("recent_activity", self.client.get(reverse("dashboard:home")).context["panels"])


class DashboardStatisticsTests(TestCase):
    def setUp(self):
        self.admin = make_user(Role.SUPER_ADMIN)
        self.client.force_login(self.admin)

    def test_member_counts_come_from_database(self):
        make_user(Role.MEMBER)  # member logins get a profile automatically
        make_user(Role.MEMBER)
        old = make_user(Role.MEMBER)
        Member.objects.filter(user=old).update(joined_on=timezone.localdate() - timedelta(days=70))
        make_user(Role.TRAINER)  # staff are not members

        stats = stat_map(self.client.get(reverse("dashboard:home")))
        self.assertEqual(stats["total_members"]["status"], "ready")
        self.assertEqual(stats["total_members"]["value"], 3)
        self.assertEqual(stats["new_members_month"]["value"], 2)

    def test_finance_and_attendance_figures_are_real_zeroes_when_empty(self):
        stats = stat_map(self.client.get(reverse("dashboard:home")))
        for key in ("today_revenue", "monthly_revenue", "today_attendance", "outstanding_payments"):
            self.assertEqual(stats[key]["status"], "ready", key)
            self.assertEqual(stats[key]["value"], 0, key)

    def test_member_growth_chart_data(self):
        make_user(Role.MEMBER)
        panels = self.client.get(reverse("dashboard:home")).context["panels"]
        growth = panels["member_growth"]["value"]
        self.assertEqual(len(growth["labels"]), 6)
        self.assertEqual(growth["data"][-1], 1)
        self.assertEqual(sum(growth["data"]), 1)

    def test_recent_activity_lists_logged_events(self):
        log_activity("user_created", "Created Member account 'zed'", actor=self.admin)
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, "Created Member account &#x27;zed&#x27;")

    def test_failing_provider_renders_error_state(self):
        original = registry._providers["total_members"]

        def broken(user):
            raise RuntimeError("database exploded")

        registry._providers["total_members"] = broken
        try:
            with self.assertLogs("apps.dashboard.registry", level="ERROR"):
                response = self.client.get(reverse("dashboard:home"))
        finally:
            registry._providers["total_members"] = original
        self.assertEqual(response.status_code, 200)
        self.assertEqual(stat_map(response)["total_members"]["status"], "error")
        self.assertNotContains(response, "database exploded")


class Phase2DashboardTests(TestCase):
    """Member and membership statistics come from real records."""

    def setUp(self):
        self.client.force_login(make_user(Role.SUPER_ADMIN))
        self.today = timezone.localdate()

    def test_empty_database(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        stats = stat_map(response)
        for key in ("total_members", "active_members", "expired_memberships", "new_members_month"):
            self.assertEqual(stats[key]["status"], "ready", key)
            self.assertEqual(stats[key]["value"], 0, key)
        panel = response.context["panels"]["upcoming_expirations"]
        self.assertEqual(panel["status"], "ready")
        self.assertEqual(panel["value"]["items"], [])
        self.assertContains(response, "No upcoming expirations")

    def test_counts_for_representative_records(self):
        from apps.memberships import services
        from apps.memberships.models import Membership
        from apps.memberships.tests.factories import make_member, make_membership, make_plan

        day = timedelta(days=1)
        plan = make_plan(months=1)
        long_ago = self.today - 120 * day
        active = make_member(joined_on=long_ago)
        make_membership(active, plan, self.today - 5 * day)
        expiring = make_member(joined_on=long_ago)
        make_membership(expiring, plan, self.today - 27 * day)  # ends in a few days
        suspended = make_member(joined_on=long_ago)
        make_membership(suspended, plan, self.today, state=Membership.State.SUSPENDED, suspended_on=self.today)
        lapsed = make_member(joined_on=long_ago)
        make_membership(lapsed, plan, self.today - 60 * day)
        renewed = make_member(joined_on=long_ago)
        make_membership(renewed, plan, self.today - 60 * day)
        services.renew_membership(renewed, plan, make_user(Role.GYM_MANAGER))  # counts as active, not lapsed
        make_member(joined_on=self.today)  # new this month, no membership
        make_member(joined_on=long_ago, status=Member.Status.INACTIVE, deactivated_on=self.today)

        response = self.client.get(reverse("dashboard:home"))
        stats = stat_map(response)
        self.assertEqual(stats["total_members"]["value"], 7)
        self.assertEqual(stats["total_members"]["note"], "1 inactive")
        self.assertEqual(stats["active_members"]["value"], 3)  # active, expiring, renewed (not suspended)
        self.assertEqual(stats["expired_memberships"]["value"], 1)  # only "lapsed"
        self.assertEqual(stats["new_members_month"]["value"], 1)
        items = response.context["panels"]["upcoming_expirations"]["value"]["items"]
        self.assertEqual([m.member for m in items], [expiring])
        growth = response.context["panels"]["member_growth"]["value"]
        self.assertEqual(growth["data"][-1], 1)



class SetupBannerTests(TestCase):
    def test_banner_shown_until_an_administrator_exists(self):
        member = make_user(Role.MEMBER)
        self.client.force_login(member)
        self.assertContains(self.client.get(reverse("dashboard:home")), "python manage.py createsuperuser")
        User.objects.create_superuser("owner", "owner@example.com", "Owner-Pass-123")
        self.assertNotContains(self.client.get(reverse("dashboard:home")), "python manage.py createsuperuser")
