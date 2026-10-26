from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.core.models import ActivityLog

from .factories import make_user

PASSWORD = "Gains-And-Grit-2026"


def payload(**overrides):
    data = {
        "full_name": "Jane Doe",
        "username": "janedoe",
        "email": "jane@example.com",
        "password1": PASSWORD,
        "password2": PASSWORD,
    }
    data.update(overrides)
    return data


class BrandingTests(TestCase):
    def test_brand_name_on_public_and_private_pages(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, "<title>Sign in · BUILDME</title>", html=False)
        self.assertContains(response, "BUILDME")
        self.client.force_login(make_user(Role.GYM_MANAGER))
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, "<title>Dashboard · BUILDME</title>", html=False)


class RegistrationPageTests(TestCase):
    url = reverse("accounts:register")

    def test_login_page_links_to_registration(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, self.url)
        self.assertContains(response, "Create new account")

    def test_registration_page_renders_with_all_fields(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for name in ("full_name", "username", "email", "password1", "password2", "csrfmiddlewaretoken"):
            self.assertContains(response, f'name="{name}"')
        self.assertContains(response, reverse("accounts:login"))  # link back to sign in

    def test_authenticated_user_is_redirected(self):
        self.client.force_login(make_user(Role.MEMBER))
        self.assertRedirects(self.client.get(self.url), reverse("dashboard:home"))

    @override_settings(REGISTRATION_ENABLED=False)
    def test_registration_can_be_disabled(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertNotContains(self.client.get(reverse("accounts:login")), "Create new account")

    def test_csrf_is_enforced(self):
        response = Client(enforce_csrf_checks=True).post(self.url, payload())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username="janedoe").exists())


class RegistrationTests(TestCase):
    url = reverse("accounts:register")

    def setUp(self):
        cache.clear()

    def test_successful_registration(self):
        response = self.client.post(self.url, payload(full_name="  Jane   van der Berg ", email="Jane@Example.COM"),
                                    follow=True)
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertContains(response, "Your account is ready")

        user = User.objects.get(username="janedoe")
        self.assertEqual((user.first_name, user.last_name), ("Jane", "van der Berg"))
        self.assertEqual(user.email, "jane@example.com")
        self.assertEqual(user.role, Role.MEMBER)
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertEqual([g.name for g in user.groups.all()], ["Member"])
        # Password is hashed, never stored in plain text.
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.password.startswith(("pbkdf2_", "argon2", "bcrypt", "md5$")))
        self.assertTrue(user.check_password(PASSWORD))
        # Registration does not sign the user in automatically.
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_registration_is_logged_without_password(self):
        self.client.post(self.url, payload())
        log = ActivityLog.objects.get(action=ActivityLog.Action.USER_REGISTERED)
        self.assertEqual(log.actor.username, "janedoe")
        self.assertFalse(ActivityLog.objects.filter(description__contains=PASSWORD).exists())

    def test_register_then_login_and_logout(self):
        self.client.post(self.url, payload())
        response = self.client.post(reverse("accounts:login"), {"username": "janedoe", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard:home"))
        response = self.client.get(reverse("dashboard:home"))
        self.assertTemplateUsed(response, "dashboard/member.html")
        self.assertContains(response, "Hi, Jane")

        # Email sign-in also works for the new account.
        self.client.post(reverse("accounts:logout"))
        response = self.client.post(reverse("accounts:login"), {"username": "jane@example.com", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard:home"))

        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 302)

    def test_new_member_cannot_reach_staff_pages(self):
        self.client.post(self.url, payload())
        self.client.post(reverse("accounts:login"), {"username": "janedoe", "password": PASSWORD})
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 403)

    def test_role_and_flags_cannot_be_injected(self):
        self.client.post(self.url, payload(role=Role.SUPER_ADMIN, is_superuser="on", is_staff="on", is_active=""))
        user = User.objects.get(username="janedoe")
        self.assertEqual(user.role, Role.MEMBER)
        self.assertFalse(user.is_superuser or user.is_staff)
        self.assertTrue(user.is_active)

    def assertRejected(self, data, field, message):
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn(field, response.context["form"].errors)
        self.assertContains(response, message)
        self.assertContains(response, "is-invalid")
        return response

    def test_duplicate_username_case_insensitive(self):
        make_user(Role.MEMBER, username="janedoe", email="other@example.com")
        self.assertRejected(payload(username="JaneDoe"), "username", "already taken")
        self.assertEqual(User.objects.count(), 1)

    def test_duplicate_email_case_insensitive(self):
        make_user(Role.MEMBER, username="someone", email="jane@example.com")
        self.assertRejected(payload(email="JANE@example.com"), "email", "already exists")

    def test_invalid_email(self):
        for bad in ("not-an-email", "jane@", "@example.com", "jane@exa mple.com"):
            self.assertRejected(payload(email=bad), "email", "Enter a valid email address")
        self.assertFalse(User.objects.exists())

    def test_mismatched_passwords(self):
        self.assertRejected(payload(password2="Something-Else-99"), "password2", "didn’t match")
        self.assertFalse(User.objects.exists())

    def test_weak_passwords_rejected_by_django_validators(self):
        self.assertRejected(payload(password1="short", password2="short"), "password2", "too short")
        self.assertRejected(payload(password1="12345678901", password2="12345678901"), "password2", "entirely numeric")
        self.assertRejected(payload(password1="password123", password2="password123"), "password2", "too common")
        self.assertRejected(payload(password1="janedoe2026", password2="janedoe2026"), "password2", "too similar")

    def test_required_fields(self):
        response = self.client.post(self.url, {})
        self.assertEqual(
            set(response.context["form"].errors), {"full_name", "username", "email", "password1", "password2"}
        )

    def test_invalid_username_characters(self):
        self.assertRejected(payload(username="jane doe!"), "username", "Enter a valid username")
        self.assertRejected(payload(username="jane@example.com"), "username", "can&#x27;t contain")

    def test_invalid_full_name(self):
        for bad in ("J4ne", "<script>", "   "):
            response = self.client.post(self.url, payload(full_name=bad))
            self.assertIn("full_name", response.context["form"].errors, bad)

    def test_email_cannot_match_existing_username(self):
        make_user(Role.MEMBER, username="coach.mike", email="mike@example.com")
        # Prevents hijacking email-based sign-in for another account.
        User.objects.filter(username="coach.mike").update(username="jane@example.com")
        self.assertRejected(payload(), "email", "can&#x27;t be used")

    def test_password_not_echoed_back_on_error(self):
        response = self.client.post(self.url, payload(password2="Mismatch-Secret-77"))
        self.assertNotContains(response, PASSWORD)
        self.assertNotContains(response, "Mismatch-Secret-77")
