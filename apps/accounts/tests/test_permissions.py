from django.contrib.auth.models import Group
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.roles import DEFAULT_ROLE_PERMISSIONS, Role, assignable_roles, can_manage_user, group_name
from apps.accounts.services import role_permission_codenames
from apps.core.models import ActivityLog

from .factories import PASSWORD, make_user


def new_user_payload(role, username="newbie", **overrides):
    data = {
        "first_name": "New",
        "last_name": "Person",
        "username": username,
        "email": f"{username}@example.com",
        "phone": "+1 555 0100",
        "role": role,
        "is_active": "on",
        "password_method": "set",
        "password1": "Fresh-Start-2024",
        "password2": "Fresh-Start-2024",
    }
    data.update(overrides)
    return data


class RoleModelTests(TestCase):
    def test_default_groups_and_permissions(self):
        for role in Role:
            self.assertTrue(Group.objects.filter(name=group_name(role)).exists())
            self.assertEqual(role_permission_codenames(role), set(DEFAULT_ROLE_PERMISSIONS[role]))

    def test_user_is_placed_in_role_group(self):
        user = make_user(Role.TRAINER)
        self.assertEqual([g.name for g in user.groups.all()], ["Trainer"])
        self.assertTrue(user.has_perm("accounts.manage_training"))
        self.assertFalse(user.has_perm("accounts.view_payments"))

    def test_role_change_resyncs_group(self):
        user = make_user(Role.TRAINER)
        user.role = Role.ACCOUNTANT
        user.save()
        user = User.objects.get(pk=user.pk)
        self.assertEqual([g.name for g in user.groups.all()], ["Accountant"])
        self.assertTrue(user.has_perm("accounts.view_payments"))
        self.assertFalse(user.has_perm("accounts.manage_training"))

    def test_only_super_admin_gets_elevated_flags(self):
        admin = make_user(Role.SUPER_ADMIN)
        manager = make_user(Role.GYM_MANAGER, is_superuser=True, is_staff=True)
        self.assertTrue(admin.is_superuser and admin.is_staff and admin.is_super_admin)
        self.assertFalse(manager.is_superuser or manager.is_staff)

    def test_createsuperuser_gets_super_admin_role(self):
        user = User.objects.create_superuser("root", "root@example.com", PASSWORD)
        self.assertEqual(user.role, Role.SUPER_ADMIN)

    def test_member_has_no_module_permissions(self):
        member = make_user(Role.MEMBER)
        self.assertFalse(member.has_perm("accounts.view_dashboard"))
        self.assertFalse(member.has_perm("accounts.view_users"))

    def test_role_hierarchy(self):
        admin = make_user(Role.SUPER_ADMIN)
        manager = make_user(Role.GYM_MANAGER)
        manager2 = make_user(Role.GYM_MANAGER)
        trainer = make_user(Role.TRAINER)
        self.assertEqual(set(assignable_roles(admin)), set(Role))
        self.assertNotIn(Role.GYM_MANAGER, assignable_roles(manager))
        self.assertNotIn(Role.SUPER_ADMIN, assignable_roles(manager))
        self.assertTrue(can_manage_user(manager, trainer))
        self.assertFalse(can_manage_user(manager, manager2))
        self.assertFalse(can_manage_user(manager, admin))
        self.assertFalse(can_manage_user(trainer, make_user(Role.MEMBER)))


class MemberAccessTests(TestCase):
    """Members may only see their own information."""

    def setUp(self):
        self.member = make_user(Role.MEMBER, username="member1")
        self.other = make_user(Role.MEMBER, username="member2")
        self.client.force_login(self.member)

    def test_member_gets_personal_dashboard(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertTemplateUsed(response, "dashboard/member.html")
        self.assertNotContains(response, "Total Members")

    def test_member_cannot_access_admin_pages(self):
        for name, args in [
            ("accounts:user_list", []),
            ("accounts:user_create", []),
            ("accounts:user_detail", [self.other.pk]),
            ("accounts:user_detail", [self.member.pk]),
            ("accounts:user_update", [self.other.pk]),
            ("accounts:user_set_password", [self.other.pk]),
            ("accounts:role_permissions", []),
            ("accounts:activity_log", []),
        ]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)
        response = self.client.post(reverse("accounts:user_toggle_active", args=[self.other.pk]))
        self.assertEqual(response.status_code, 403)
        self.other.refresh_from_db()
        self.assertTrue(self.other.is_active)

    def test_member_sees_own_profile(self):
        response = self.client.get(reverse("accounts:profile"))
        self.assertContains(response, "member1@example.com")
        self.assertNotContains(response, "member2@example.com")

    def test_profile_edit_cannot_change_role_or_status(self):
        response = self.client.post(reverse("accounts:profile_edit"), {
            "first_name": "Changed", "last_name": "Name", "email": "member1@example.com", "phone": "",
            "role": Role.SUPER_ADMIN, "is_active": "", "is_superuser": "on",
        })
        self.assertRedirects(response, reverse("accounts:profile"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.first_name, "Changed")
        self.assertEqual(self.member.role, Role.MEMBER)
        self.assertFalse(self.member.is_superuser)
        self.assertTrue(self.member.is_active)

    def test_profile_email_must_be_unique(self):
        response = self.client.post(reverse("accounts:profile_edit"), {
            "first_name": "A", "last_name": "B", "email": "MEMBER2@example.com", "phone": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")

    def test_member_navigation_hides_admin_modules(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertNotContains(response, reverse("accounts:user_list"))
        self.assertNotContains(response, reverse("accounts:role_permissions"))


class StaffRoleAccessTests(TestCase):
    def test_receptionist_cannot_manage_users(self):
        self.client.force_login(make_user(Role.RECEPTIONIST))
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:user_create")).status_code, 403)

    def test_trainer_and_accountant_cannot_manage_roles(self):
        for role in (Role.TRAINER, Role.ACCOUNTANT, Role.GYM_MANAGER):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(reverse("accounts:role_permissions")).status_code, 403, role)


class ManagerUserManagementTests(TestCase):
    def setUp(self):
        self.admin = make_user(Role.SUPER_ADMIN, username="boss")
        self.manager = make_user(Role.GYM_MANAGER, username="manager")
        self.peer = make_user(Role.GYM_MANAGER, username="peer")
        self.trainer = make_user(Role.TRAINER, username="coach")
        self.client.force_login(self.manager)

    def test_list_search_and_filters(self):
        url = reverse("accounts:user_list")
        response = self.client.get(url, {"q": "coach"})
        self.assertContains(response, "coach@example.com")
        self.assertNotContains(response, "peer@example.com")
        response = self.client.get(url, {"role": Role.SUPER_ADMIN})
        self.assertContains(response, "boss@example.com")
        self.assertNotContains(response, "coach@example.com")
        response = self.client.get(url, {"q": "zzz-no-match"})
        self.assertContains(response, "No matching users")

    def test_list_paginates(self):
        for i in range(20):
            make_user(Role.MEMBER, username=f"m{i:02d}")
        response = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(len(response.context["users"]), 15)
        self.assertTrue(response.context["is_paginated"])
        response = self.client.get(reverse("accounts:user_list"), {"page": 2})
        self.assertEqual(response.status_code, 200)

    def test_manager_can_create_receptionist(self):
        response = self.client.post(reverse("accounts:user_create"), new_user_payload(Role.RECEPTIONIST))
        user = User.objects.get(username="newbie")
        self.assertRedirects(response, user.get_absolute_url())
        self.assertEqual(user.role, Role.RECEPTIONIST)
        self.assertEqual(user.created_by, self.manager)
        self.assertTrue(user.check_password("Fresh-Start-2024"))
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.USER_CREATED).exists())

    def test_manager_cannot_create_privileged_roles(self):
        for role in (Role.SUPER_ADMIN, Role.GYM_MANAGER):
            response = self.client.post(reverse("accounts:user_create"), new_user_payload(role, username=f"x{role}"))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(User.objects.filter(username=f"x{role}").exists())

    def test_weak_and_mismatched_passwords_rejected(self):
        response = self.client.post(reverse("accounts:user_create"),
                                    new_user_payload(Role.MEMBER, password1="123", password2="123"))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("accounts:user_create"),
                                    new_user_payload(Role.MEMBER, password2="Different-Pass-99"))
        self.assertContains(response, "didn&#x27;t match")
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_duplicate_username_and_email_rejected(self):
        response = self.client.post(reverse("accounts:user_create"),
                                    new_user_payload(Role.MEMBER, username="COACH", email="Coach@Example.com"))
        self.assertContains(response, "already taken")
        self.assertContains(response, "already exists")

    def test_create_with_email_invite(self):
        response = self.client.post(reverse("accounts:user_create"),
                                    new_user_payload(Role.MEMBER, password_method="email", password1="", password2=""))
        user = User.objects.get(username="newbie")
        self.assertRedirects(response, user.get_absolute_url())
        self.assertFalse(user.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/password/reset/", mail.outbox[0].body)

    def test_manager_can_edit_and_deactivate_lower_role(self):
        response = self.client.post(reverse("accounts:user_update", args=[self.trainer.pk]), {
            "first_name": "Coach", "last_name": "Carter", "username": "coach",
            "email": "coach@example.com", "phone": "", "role": Role.RECEPTIONIST,
        })
        self.assertRedirects(response, self.trainer.get_absolute_url())
        self.trainer.refresh_from_db()
        self.assertEqual(self.trainer.role, Role.RECEPTIONIST)
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.ROLE_CHANGED).exists())

        self.client.post(reverse("accounts:user_toggle_active", args=[self.trainer.pk]))
        self.trainer.refresh_from_db()
        self.assertFalse(self.trainer.is_active)
        self.client.post(reverse("accounts:user_toggle_active", args=[self.trainer.pk]))
        self.trainer.refresh_from_db()
        self.assertTrue(self.trainer.is_active)

    def test_manager_cannot_promote_to_super_admin(self):
        response = self.client.post(reverse("accounts:user_update", args=[self.trainer.pk]), {
            "first_name": "Coach", "last_name": "Carter", "username": "coach",
            "email": "coach@example.com", "phone": "", "role": Role.SUPER_ADMIN,
        })
        self.assertEqual(response.status_code, 200)
        self.trainer.refresh_from_db()
        self.assertEqual(self.trainer.role, Role.TRAINER)

    def test_manager_cannot_touch_peers_or_superiors(self):
        for target in (self.peer, self.admin):
            self.assertEqual(self.client.get(reverse("accounts:user_update", args=[target.pk])).status_code, 403)
            self.assertEqual(self.client.get(reverse("accounts:user_set_password", args=[target.pk])).status_code, 403)
            self.assertEqual(
                self.client.post(reverse("accounts:user_toggle_active", args=[target.pk])).status_code, 403
            )
            target.refresh_from_db()
            self.assertTrue(target.is_active)

    def test_manager_can_view_but_not_edit_superior(self):
        response = self.client.get(reverse("accounts:user_detail", args=[self.admin.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("accounts:user_update", args=[self.admin.pk]))

    def test_toggle_requires_post(self):
        response = self.client.get(reverse("accounts:user_toggle_active", args=[self.trainer.pk]))
        self.assertEqual(response.status_code, 405)

    def test_admin_set_password(self):
        response = self.client.post(reverse("accounts:user_set_password", args=[self.trainer.pk]), {
            "new_password1": "Coach-New-Pass-77", "new_password2": "Coach-New-Pass-77",
        })
        self.assertRedirects(response, self.trainer.get_absolute_url())
        self.trainer.refresh_from_db()
        self.assertTrue(self.trainer.check_password("Coach-New-Pass-77"))


class SuperAdminTests(TestCase):
    def setUp(self):
        self.admin = make_user(Role.SUPER_ADMIN, username="boss")
        self.client.force_login(self.admin)

    def test_can_create_gym_manager(self):
        self.client.post(reverse("accounts:user_create"), new_user_payload(Role.GYM_MANAGER, username="gm"))
        self.assertEqual(User.objects.get(username="gm").role, Role.GYM_MANAGER)

    def test_cannot_deactivate_self(self):
        self.client.post(reverse("accounts:user_toggle_active", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_cannot_change_own_role(self):
        self.client.post(reverse("accounts:user_update", args=[self.admin.pk]), {
            "first_name": "Boss", "last_name": "Admin", "username": "boss",
            "email": "boss@example.com", "phone": "", "role": Role.MEMBER,
        })
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, Role.SUPER_ADMIN)

    def test_role_permissions_page_updates_groups(self):
        url = reverse("accounts:role_permissions")
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {
            "perm_trainer": ["view_dashboard", "view_payments"],
            "perm_member": ["view_dashboard"],
            "perm_receptionist": ["view_dashboard", "manage_roles"],  # reserved: must be ignored
            "perm_super_admin": [],  # Super Admin cannot be reduced
        })
        self.assertRedirects(response, url)
        self.assertEqual(role_permission_codenames(Role.TRAINER), {"view_dashboard", "view_payments"})
        self.assertEqual(role_permission_codenames(Role.MEMBER), set())  # Member role is portal-only (locked)
        self.assertEqual(role_permission_codenames(Role.RECEPTIONIST), {"view_dashboard"})
        self.assertEqual(role_permission_codenames(Role.SUPER_ADMIN), set(DEFAULT_ROLE_PERMISSIONS[Role.SUPER_ADMIN]))
        # Roles omitted from the POST lose their permissions (unchecked boxes).
        self.assertEqual(role_permission_codenames(Role.ACCOUNTANT), set())
        trainer = make_user(Role.TRAINER)
        self.assertTrue(trainer.has_perm("accounts.view_payments"))
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.PERMISSIONS_UPDATED).exists())

    def test_inactive_super_admin_has_no_super_admin_access(self):
        other = make_user(Role.SUPER_ADMIN)
        other.is_active = False
        self.assertFalse(other.is_super_admin)


class MemberRolePortalOnlyTests(TestCase):
    """Approved: the Member role can never hold staff permissions."""

    def test_matrix_ignores_member_column(self):
        admin = make_user(Role.SUPER_ADMIN)
        self.client.force_login(admin)
        response = self.client.get(reverse("accounts:role_permissions"))
        member_col = [c for c in response.context["role_columns"] if c["value"] == Role.MEMBER][0]
        self.assertTrue(member_col["locked"])
        self.client.post(reverse("accounts:role_permissions"), {
            "perm_member": ["view_activity_log", "view_attendance", "view_training"],
            "perm_trainer": ["view_dashboard"],
        })
        self.assertEqual(role_permission_codenames(Role.MEMBER), set())
        self.assertEqual(role_permission_codenames(Role.TRAINER), {"view_dashboard"})

    def test_service_refuses_member_grants(self):
        from apps.accounts.services import set_role_permissions
        with self.assertRaises(ValueError):
            set_role_permissions(Role.MEMBER, ["view_training"])

    def test_sync_clears_member_group(self):
        from django.contrib.auth.models import Permission
        from apps.accounts.services import ensure_role_groups
        group = Group.objects.get(name="Member")
        group.permissions.add(Permission.objects.get(codename="view_activity_log"))
        ensure_role_groups()
        self.assertFalse(group.permissions.exists())
        member = make_user(Role.MEMBER)
        self.client.force_login(member)
        self.assertEqual(self.client.get(reverse("accounts:activity_log")).status_code, 403)
