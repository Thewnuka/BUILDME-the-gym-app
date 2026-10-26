from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.members.models import Member
from apps.memberships.models import Membership, MembershipStatus

from .factories import make_member, make_membership, make_plan

DAY = timedelta(days=1)


class MembershipPagesTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.staff = make_user(Role.GYM_MANAGER)
        self.client.force_login(self.staff)
        self.plan = make_plan(name="Monthly", months=1, price="5000")
        self.member = make_member(full_name="Ruwan Kumara", joined_on=self.today - 200 * DAY)

    def test_all_pages_render(self):
        m = make_membership(self.member, self.plan, self.today - 3 * DAY)
        urls = [
            reverse("members:list"), reverse("members:create"), reverse("members:detail", args=[self.member.pk]),
            reverse("members:update", args=[self.member.pk]),
            reverse("memberships:list"), reverse("memberships:expiring"), reverse("memberships:expired"),
            reverse("memberships:detail", args=[m.pk]), reverse("memberships:renew", args=[m.pk]),
            reverse("memberships:assign", args=[make_member().pk]),
            reverse("memberships:plan_list"), reverse("memberships:plan_create"),
            reverse("memberships:plan_detail", args=[self.plan.pk]),
            reverse("memberships:plan_update", args=[self.plan.pk]),
            reverse("dashboard:home"),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_assign_via_form(self):
        url = reverse("memberships:assign", args=[self.member.pk])
        response = self.client.post(url, {"plan": self.plan.pk, "start_date": self.today.isoformat(), "notes": ""})
        m = Membership.objects.get()
        self.assertRedirects(response, m.get_absolute_url())
        self.assertEqual(m.status, MembershipStatus.ACTIVE)

    def test_assign_overlap_shows_error(self):
        make_membership(self.member, self.plan, self.today)
        response = self.client.post(reverse("memberships:assign", args=[self.member.pk]),
                                    {"plan": self.plan.pk, "start_date": self.today.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "overlap")
        self.assertEqual(Membership.objects.count(), 1)

    def test_assign_rejects_inactive_plan_via_crafted_post(self):
        self.plan.is_active = False
        self.plan.save()
        response = self.client.post(reverse("memberships:assign", args=[self.member.pk]),
                                    {"plan": self.plan.pk, "start_date": self.today.isoformat()})
        self.assertIn("plan", response.context["form"].errors)
        self.assertFalse(Membership.objects.exists())

    def test_renew_suspend_reactivate_cancel_via_views(self):
        m = make_membership(self.member, self.plan, self.today - 3 * DAY)
        response = self.client.post(reverse("memberships:renew", args=[m.pk]), {"plan": self.plan.pk})
        renewal = Membership.objects.exclude(pk=m.pk).get()
        self.assertRedirects(response, renewal.get_absolute_url())
        self.assertEqual(renewal.start_date, m.end_date + DAY)

        self.client.post(reverse("memberships:suspend", args=[m.pk]), {"reason": "Travel"})
        m.refresh_from_db()
        self.assertEqual(m.status, MembershipStatus.SUSPENDED)
        self.client.post(reverse("memberships:reactivate", args=[m.pk]))
        m.refresh_from_db()
        self.assertEqual(m.status, MembershipStatus.ACTIVE)
        response = self.client.post(reverse("memberships:cancel", args=[m.pk]), {"reason": ""}, follow=True)
        self.assertContains(response, "Please give a reason")
        self.client.post(reverse("memberships:cancel", args=[m.pk]), {"reason": "Moving away"})
        m.refresh_from_db()
        self.assertEqual(m.status, MembershipStatus.CANCELLED)

    def test_invalid_transition_shows_error_message(self):
        m = make_membership(self.member, self.plan, self.today - 60 * DAY)  # expired
        response = self.client.post(reverse("memberships:suspend", args=[m.pk]), {"reason": "Test"}, follow=True)
        self.assertContains(response, "Only active memberships can be suspended")

    def test_lifecycle_actions_require_post(self):
        m = make_membership(self.member, self.plan, self.today)
        for name in ("suspend", "reactivate", "cancel"):
            self.assertEqual(self.client.get(reverse(f"memberships:{name}", args=[m.pk])).status_code, 405)

    def test_expiring_and_expired_lists(self):
        soon = make_membership(make_member(full_name="Soon Ending"), self.plan, self.today - 27 * DAY)
        make_membership(make_member(full_name="Lapsed Person"), self.plan, self.today - 60 * DAY)
        make_membership(make_member(full_name="Gone Inactive", status=Member.Status.INACTIVE,
                                    deactivated_on=self.today), self.plan, self.today - 60 * DAY)
        response = self.client.get(reverse("memberships:expiring"))
        self.assertContains(response, "Soon Ending")
        self.assertNotContains(response, "Lapsed Person")
        self.assertEqual(self.client.get(reverse("memberships:expiring"), {"days": "999"}).context["days"], 7)
        response = self.client.get(reverse("memberships:expired"))
        self.assertContains(response, "Lapsed Person")
        self.assertNotContains(response, "Gone Inactive")
        self.assertNotContains(response, "Soon Ending")
        response = self.client.get(reverse("memberships:expired"), {"include_inactive": "1"})
        self.assertContains(response, "Gone Inactive")
        self.assertEqual(soon.status, MembershipStatus.ACTIVE)

    def test_membership_list_filters(self):
        make_membership(self.member, self.plan, self.today)
        make_membership(make_member(full_name="Old Timer"), self.plan, self.today - 90 * DAY)
        response = self.client.get(reverse("memberships:list"), {"status": "expired"})
        self.assertContains(response, "Old Timer")
        self.assertNotContains(response, "Ruwan Kumara")
        response = self.client.get(reverse("memberships:list"), {"q": self.member.member_id})
        self.assertContains(response, "Ruwan Kumara")


class MembershipPermissionTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.plan = make_plan()
        self.member = make_member(joined_on=today - 100 * DAY)
        self.m = make_membership(self.member, self.plan, today - 3 * DAY)

    def test_view_only_roles_cannot_change_memberships(self):
        for role in (Role.ACCOUNTANT,):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(reverse("memberships:detail", args=[self.m.pk])).status_code, 200)
            for name, data in [("suspend", {"reason": "abc"}), ("cancel", {"reason": "abc"}), ("reactivate", {}),
                               ("renew", {"plan": self.plan.pk})]:
                response = self.client.post(reverse(f"memberships:{name}", args=[self.m.pk]), data)
                self.assertEqual(response.status_code, 403, (role, name))
            response = self.client.post(reverse("memberships:assign", args=[self.member.pk]),
                                        {"plan": self.plan.pk, "start_date": timezone.localdate().isoformat()})
            self.assertEqual(response.status_code, 403)
        self.m.refresh_from_db()
        self.assertEqual(self.m.status, MembershipStatus.ACTIVE)
        self.assertEqual(Membership.objects.count(), 1)

    def test_trainer_and_member_cannot_see_memberships(self):
        for role in (Role.TRAINER, Role.MEMBER):
            self.client.force_login(make_user(role))
            for url in (reverse("memberships:list"), reverse("memberships:detail", args=[self.m.pk]),
                        reverse("memberships:plan_list"), reverse("memberships:expired")):
                self.assertEqual(self.client.get(url).status_code, 403, (role, url))

    def test_member_sees_only_own_membership_on_dashboard(self):
        login = make_user(Role.MEMBER, username="ownerlogin")
        own = Member.objects.get(user=login)
        own.joined_on = timezone.localdate() - 10 * DAY
        own.save()
        mine = make_membership(own, make_plan(name="Mine Plan"), timezone.localdate())
        self.client.force_login(login)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.context["my_current"], mine)
        self.assertContains(response, "Mine Plan")
        self.assertNotContains(response, self.member.full_name)


class ReceptionistMembershipAccessTests(TestCase):
    """Approved default: front desk can assign, renew, suspend and cancel memberships."""

    def test_receptionist_can_manage_memberships(self):
        today = timezone.localdate()
        plan = make_plan()
        member = make_member(joined_on=today - 100 * DAY)
        self.client.force_login(make_user(Role.RECEPTIONIST))
        response = self.client.post(reverse("memberships:assign", args=[member.pk]),
                                    {"plan": plan.pk, "start_date": today.isoformat()})
        m = Membership.objects.get()
        self.assertRedirects(response, m.get_absolute_url())
        self.client.post(reverse("memberships:suspend", args=[m.pk]), {"reason": "Travel"})
        m.refresh_from_db()
        self.assertEqual(m.status, MembershipStatus.SUSPENDED)
        # Plan prices remain off-limits to receptionists.
        self.assertEqual(self.client.get(reverse("memberships:plan_create")).status_code, 403)
