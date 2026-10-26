import re

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.roles import Role
from apps.core.models import ActivityLog

from .factories import PASSWORD, make_user


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user(Role.RECEPTIONIST, username="frontdesk")
        self.url = reverse("accounts:login")

    def test_login_page_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_login_with_username(self):
        response = self.client.post(self.url, {"username": "frontdesk", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard:home"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_login_with_email_case_insensitive(self):
        response = self.client.post(self.url, {"username": "FrontDesk@Example.com", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard:home"))

    def test_wrong_password_shows_generic_error(self):
        response = self.client.post(self.url, {"username": "frontdesk", "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid credentials or inactive account")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_cannot_login(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(self.url, {"username": "frontdesk", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(LOGIN_MAX_ATTEMPTS=3)
    def test_lockout_after_repeated_failures(self):
        for _ in range(3):
            self.client.post(self.url, {"username": "frontdesk", "password": "wrong"})
        # Even the correct password is refused while locked out.
        response = self.client.post(self.url, {"username": "frontdesk", "password": PASSWORD})
        self.assertContains(response, "Too many failed sign-in attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_remember_me_unchecked_expires_at_browser_close(self):
        self.client.post(self.url, {"username": "frontdesk", "password": PASSWORD})
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_remember_me_checked_keeps_session(self):
        self.client.post(self.url, {"username": "frontdesk", "password": PASSWORD, "remember_me": "on"})
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_open_redirect_is_blocked(self):
        response = self.client.post(
            self.url, {"username": "frontdesk", "password": PASSWORD, "next": "https://evil.example.com/"}
        )
        self.assertRedirects(response, reverse("dashboard:home"))

    def test_login_and_failure_are_logged(self):
        self.client.post(self.url, {"username": "frontdesk", "password": "nope"})
        self.client.post(self.url, {"username": "frontdesk", "password": PASSWORD})
        actions = set(ActivityLog.objects.values_list("action", flat=True))
        self.assertIn(ActivityLog.Action.LOGIN_FAILED, actions)
        self.assertIn(ActivityLog.Action.LOGIN, actions)
        # The attempted password must never be stored.
        self.assertFalse(ActivityLog.objects.filter(description__contains="nope").exists())

    def test_authenticated_user_is_redirected_away_from_login(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(self.url), reverse("dashboard:home"))

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(self.url, {"username": "frontdesk", "password": PASSWORD})
        self.assertEqual(response.status_code, 403)


class LogoutTests(TestCase):
    def test_logout_requires_post(self):
        user = make_user(Role.TRAINER)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)


class PrivatePagesTests(TestCase):
    def test_anonymous_users_are_redirected_to_login(self):
        user = make_user(Role.MEMBER)
        for name, args in [
            ("dashboard:home", []),
            ("accounts:profile", []),
            ("accounts:profile_edit", []),
            ("accounts:password_change", []),
            ("accounts:user_list", []),
            ("accounts:user_create", []),
            ("accounts:user_detail", [user.pk]),
            ("accounts:role_permissions", []),
            ("accounts:activity_log", []),
        ]:
            url = reverse(name, args=args)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertTrue(response.url.startswith(reverse("accounts:login")), url)

    def test_deactivated_user_loses_access_immediately(self):
        user = make_user(Role.RECEPTIONIST)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)
        user.is_active = False
        user.save()
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 302)


class PasswordChangeTests(TestCase):
    def test_change_password(self):
        user = make_user(Role.MEMBER, username="lifter")
        self.client.force_login(user)
        response = self.client.post(reverse("accounts:password_change"), {
            "old_password": PASSWORD,
            "new_password1": "An0ther-Secure-Pass",
            "new_password2": "An0ther-Secure-Pass",
        })
        self.assertRedirects(response, reverse("accounts:profile"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("An0ther-Secure-Pass"))
        # Session stays valid after changing password.
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.PASSWORD_CHANGED).exists())

    def test_wrong_old_password_rejected(self):
        user = make_user(Role.MEMBER)
        self.client.force_login(user)
        response = self.client.post(reverse("accounts:password_change"), {
            "old_password": "incorrect",
            "new_password1": "An0ther-Secure-Pass",
            "new_password2": "An0ther-Secure-Pass",
        })
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))


class PasswordResetTests(TestCase):
    def test_full_reset_flow(self):
        user = make_user(Role.MEMBER, username="resetme")
        response = self.client.post(reverse("accounts:password_reset"), {"email": "resetme@example.com"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r"https?://[^/]+(/accounts/password/reset/\S+/)", mail.outbox[0].body).group(1)

        response = self.client.get(link, follow=True)
        self.assertContains(response, "Choose a new password")
        response = self.client.post(response.redirect_chain[-1][0], {
            "new_password1": "Brand-New-Pass-42",
            "new_password2": "Brand-New-Pass-42",
        })
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("Brand-New-Pass-42"))

    def test_unknown_email_does_not_reveal_existence(self):
        response = self.client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_token_shows_expired_message(self):
        response = self.client.get(reverse("accounts:password_reset_confirm", args=["MQ", "bad-token"]))
        self.assertContains(response, "Link expired")
