import io
import shutil
import tempfile
from datetime import timedelta

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.core.models import ActivityLog
from apps.members.models import Member, normalize_phone
from apps.memberships.tests.factories import make_member, make_membership, make_plan

MEDIA = tempfile.mkdtemp(prefix="gym-test-media-")


def image_file(name="photo.png", fmt="PNG", size=(40, 40)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 80, 20)).save(buf, format=fmt)
    return SimpleUploadedFile(name, buf.getvalue(), content_type=f"image/{fmt.lower()}")


def payload(**overrides):
    data = {
        "full_name": "Kasun Perera", "phone": "077 123 4567", "email": "kasun@example.com",
        "date_of_birth": "1995-04-12", "address": "12 Galle Road, Colombo",
        "emergency_contact_name": "Nimali Perera", "emergency_contact_phone": "0711112222",
        "joined_on": timezone.localdate().isoformat(), "fitness_goals": "Build strength",
    }
    data.update(overrides)
    return data


class PhoneNormalisationTests(TestCase):
    def test_formats(self):
        self.assertEqual(normalize_phone("077 123-4567"), "+94771234567")
        self.assertEqual(normalize_phone("+94 77 123 4567"), "+94771234567")
        self.assertEqual(normalize_phone("0094771234567"), "+94771234567")
        self.assertEqual(normalize_phone("(077) 1234567"), "+94771234567")
        self.assertEqual(normalize_phone(""), "")


class MemberModelTests(TestCase):
    def test_member_ids_are_unique_and_sequential(self):
        a, b, c = make_member(), make_member(), make_member()
        ids = [a.member_id, b.member_id, c.member_id]
        self.assertEqual(len(set(ids)), 3)
        self.assertTrue(all(i.startswith("BM-") and len(i) == 9 for i in ids))
        self.assertEqual(Member.objects.get(pk=a.pk).member_id, a.member_id)  # persisted

    def test_member_id_not_reassigned_on_save(self):
        m = make_member()
        original = m.member_id
        m.full_name = "Renamed"
        m.save()
        self.assertEqual(Member.objects.get(pk=m.pk).member_id, original)

    def test_database_rejects_duplicate_phone_and_email(self):
        make_member(phone="0771234567", email="a@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_member(phone="+94 77 123 4567")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_member(email="A@EXAMPLE.com")

    def test_blank_email_allowed_for_many_members(self):
        make_member(email="")
        make_member(email="")
        self.assertEqual(Member.objects.filter(email="").count(), 2)

    def test_database_rejects_dob_after_registration(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_member(date_of_birth=timezone.localdate(), joined_on=timezone.localdate() - timedelta(days=5))


@override_settings(MEDIA_ROOT=MEDIA)
class MemberRegistrationTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.staff = make_user(Role.RECEPTIONIST)
        self.client.force_login(self.staff)
        self.url = reverse("members:create")

    def test_register_member(self):
        response = self.client.post(self.url, payload())
        member = Member.objects.get(email="kasun@example.com")
        self.assertRedirects(response, member.get_absolute_url())
        self.assertEqual(member.phone, "+94771234567")
        self.assertEqual(member.emergency_contact_phone, "+94711112222")
        self.assertEqual(member.created_by, self.staff)
        self.assertTrue(member.member_id.startswith("BM-"))
        self.assertIsNone(member.user)
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.MEMBER_REGISTERED).exists())

    def test_register_with_photo(self):
        response = self.client.post(self.url, payload(photo=image_file()))
        member = Member.objects.get()
        self.assertRedirects(response, member.get_absolute_url())
        self.assertTrue(member.photo.name.startswith("members/photos/"))
        self.assertNotIn("photo.png", member.photo.name)  # random, safe file name
        photo = self.client.get(reverse("members:photo", args=[member.pk]))
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo["Content-Type"], "image/png")

    def test_rejects_non_image_and_disguised_files(self):
        fake = SimpleUploadedFile("photo.png", b"<?php echo 'hi'; ?>", content_type="image/png")
        response = self.client.post(self.url, payload(photo=fake))
        self.assertIn("photo", response.context["form"].errors)
        gif = image_file("anim.gif", "GIF")
        response = self.client.post(self.url, payload(photo=gif))
        self.assertIn("photo", response.context["form"].errors)
        self.assertFalse(Member.objects.exists())

    @override_settings(MEMBER_PHOTO_MAX_BYTES=1024)
    def test_rejects_oversized_photo(self):
        response = self.client.post(self.url, payload(photo=image_file(size=(400, 400))))
        self.assertContains(response, "too large")
        self.assertFalse(Member.objects.exists())

    def test_required_and_invalid_fields(self):
        response = self.client.post(self.url, {"full_name": "", "phone": "", "joined_on": ""})
        errors = response.context["form"].errors
        self.assertTrue({"full_name", "phone", "joined_on"} <= set(errors))
        response = self.client.post(self.url, payload(email="not-an-email", phone="abc"))
        self.assertTrue({"email", "phone"} <= set(response.context["form"].errors))
        self.assertFalse(Member.objects.exists())

    def test_date_validation(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        response = self.client.post(self.url, payload(date_of_birth=tomorrow, joined_on=tomorrow))
        errors = response.context["form"].errors
        self.assertIn("date_of_birth", errors)
        self.assertIn("joined_on", errors)
        response = self.client.post(self.url, payload(date_of_birth="1850-01-01"))
        self.assertIn("date_of_birth", response.context["form"].errors)

    def test_emergency_contact_must_be_complete(self):
        response = self.client.post(self.url, payload(emergency_contact_phone=""))
        self.assertIn("emergency_contact_phone", response.context["form"].errors)
        response = self.client.post(self.url, payload(emergency_contact_phone="077 123 4567"))
        self.assertIn("emergency_contact_phone", response.context["form"].errors)  # same as member

    def test_duplicate_phone_and_email_rejected(self):
        existing = make_member(phone="0771234567", email="kasun@example.com")
        response = self.client.post(self.url, payload(phone="+94 77 123 4567", email="KASUN@example.com"))
        self.assertContains(response, f"already registered to member {existing.member_id}")
        self.assertEqual(Member.objects.count(), 1)

    def test_optional_email(self):
        response = self.client.post(self.url, payload(email=""))
        self.assertEqual(response.status_code, 302)

    def test_create_portal_login_sends_setup_email(self):
        self.client.post(self.url, payload(create_login="on"))
        member = Member.objects.get()
        self.assertIsNotNone(member.user)
        self.assertEqual(member.user.role, Role.MEMBER)
        self.assertFalse(member.user.has_usable_password())
        self.assertEqual(Member.objects.count(), 1)  # no duplicate profile from the signal
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["kasun@example.com"])

    def test_portal_login_requires_email(self):
        response = self.client.post(self.url, payload(email="", create_login="on"))
        self.assertIn("email", response.context["form"].errors)


class MemberListTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user(Role.TRAINER))  # trainers can view members
        today = timezone.localdate()
        self.alice = make_member(full_name="Alice Fernando", phone="0770000001", email="alice@example.com",
                                 joined_on=today - timedelta(days=40))
        self.bob = make_member(full_name="Bob Silva", phone="0770000002", joined_on=today)
        self.carol = make_member(full_name="Carol Dias", phone="0770000003",
                                 status=Member.Status.INACTIVE, deactivated_on=today)
        plan = make_plan()
        make_membership(self.alice, plan, today - timedelta(days=5))
        self.url = reverse("members:list")

    def names(self, **params):
        return [m.full_name for m in self.client.get(self.url, params).context["members"]]

    def test_search_by_name_id_phone_email(self):
        self.assertEqual(self.names(q="alice"), ["Alice Fernando"])
        self.assertEqual(self.names(q=self.bob.member_id), ["Bob Silva"])
        self.assertEqual(self.names(q="077 000 0003"), ["Carol Dias"])
        self.assertEqual(self.names(q="alice@example"), ["Alice Fernando"])
        self.assertEqual(self.names(q="nobody"), [])

    def test_filters(self):
        self.assertEqual(sorted(self.names(status="active")), ["Alice Fernando", "Bob Silva"])
        self.assertEqual(self.names(status="inactive"), ["Carol Dias"])
        self.assertEqual(self.names(membership="current"), ["Alice Fernando"])
        self.assertEqual(sorted(self.names(membership="none")), ["Bob Silva", "Carol Dias"])
        since = (timezone.localdate() - timedelta(days=10)).isoformat()
        self.assertNotIn("Alice Fernando", self.names(joined_from=since))
        self.assertEqual(self.names(joined_to=(timezone.localdate() - timedelta(days=30)).isoformat()), ["Alice Fernando"])

    def test_invalid_filter_values_are_ignored(self):
        response = self.client.get(self.url, {"status": "evil", "joined_from": "not-a-date", "sort": "password"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["members"]), 3)

    def test_sorting_allowlist(self):
        self.assertEqual(self.names(sort="name"), ["Alice Fernando", "Bob Silva", "Carol Dias"])
        self.assertEqual(self.names(sort="-name"), ["Carol Dias", "Bob Silva", "Alice Fernando"])
        self.assertEqual(self.names(sort="joined")[0], "Alice Fernando")

    def test_pagination(self):
        for i in range(25):
            make_member(full_name=f"Extra {i:02d}")
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["members"]), 20)
        self.assertEqual(len(self.client.get(self.url, {"page": 2}).context["members"]), 8)


class MemberUpdateAndDeactivationTests(TestCase):
    def setUp(self):
        self.manager = make_user(Role.GYM_MANAGER)
        self.client.force_login(self.manager)
        self.member = make_member(full_name="Dilan Jay", phone="0779998888")

    def test_edit_member(self):
        data = payload(full_name="Dilan Jayasinghe", phone="0779998888", email="dilan@example.com")
        response = self.client.post(reverse("members:update", args=[self.member.pk]), data)
        self.assertRedirects(response, self.member.get_absolute_url())
        self.member.refresh_from_db()
        self.assertEqual(self.member.full_name, "Dilan Jayasinghe")
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.MEMBER_UPDATED).exists())

    def test_edit_rejects_duplicate_of_another_member(self):
        other = make_member(phone="0771110000")
        response = self.client.post(reverse("members:update", args=[self.member.pk]), payload(phone="0771110000"))
        self.assertContains(response, other.member_id)

    def test_deactivate_preserves_history_and_disables_login(self):
        user = make_user(Role.MEMBER, username="dilanlogin")
        Member.objects.filter(user=user).delete()
        self.member.user = user
        self.member.save()
        old = make_membership(self.member, make_plan(), timezone.localdate() - timedelta(days=90))
        response = self.client.post(reverse("members:deactivate", args=[self.member.pk]), {"reason": "Moved abroad"})
        self.assertRedirects(response, self.member.get_absolute_url())
        self.member.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(self.member.status, Member.Status.INACTIVE)
        self.assertEqual(self.member.deactivation_reason, "Moved abroad")
        self.assertFalse(user.is_active)
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())
        self.assertTrue(self.member.memberships.filter(pk=old.pk).exists())

        self.client.post(reverse("members:reactivate", args=[self.member.pk]))
        self.member.refresh_from_db()
        user.refresh_from_db()
        self.assertTrue(self.member.is_active and user.is_active)
        self.assertIsNone(self.member.deactivated_on)

    def test_deactivation_blocked_while_membership_open(self):
        make_membership(self.member, make_plan(), timezone.localdate())
        response = self.client.post(reverse("members:deactivate", args=[self.member.pk]), {"reason": "Leaving"}, follow=True)
        self.assertContains(response, "Cancel it before deactivating")
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)

    def test_deactivation_requires_reason_and_post(self):
        self.client.post(reverse("members:deactivate", args=[self.member.pk]), {"reason": ""})
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)
        self.assertEqual(self.client.get(reverse("members:deactivate", args=[self.member.pk])).status_code, 405)


class MemberPermissionTests(TestCase):
    def setUp(self):
        self.member = make_member()

    def test_view_only_roles_cannot_change_members(self):
        for role in (Role.TRAINER, Role.ACCOUNTANT):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(reverse("members:list")).status_code, 200, role)
            self.assertEqual(self.client.get(reverse("members:create")).status_code, 403, role)
            self.assertEqual(self.client.get(reverse("members:update", args=[self.member.pk])).status_code, 403)
            response = self.client.post(reverse("members:deactivate", args=[self.member.pk]), {"reason": "x" * 5})
            self.assertEqual(response.status_code, 403)
            response = self.client.post(reverse("members:create"), payload())
            self.assertEqual(response.status_code, 403)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)
        self.assertEqual(Member.objects.count(), 1)

    def test_member_login_cannot_see_other_members(self):
        login = make_user(Role.MEMBER)
        self.client.force_login(login)
        self.assertEqual(self.client.get(reverse("members:list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("members:detail", args=[self.member.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("members:photo", args=[self.member.pk])).status_code, 403)


class MemberLoginLinkTests(TestCase):
    def test_member_login_gets_profile_automatically(self):
        user = make_user(Role.MEMBER, username="selfsignup")
        profile = Member.objects.get(user=user)
        self.assertEqual(profile.email, user.email)
        self.assertTrue(profile.member_id)

    def test_staff_logins_get_no_profile(self):
        make_user(Role.TRAINER)
        self.assertFalse(Member.objects.exists())

    def test_staff_created_login_links_existing_profile_by_email(self):
        profile = make_member(email="walkin@example.com")
        user = make_user(Role.MEMBER, username="walkin", email="walkin@example.com")
        profile.refresh_from_db()
        self.assertEqual(profile.user, user)
        self.assertEqual(Member.objects.count(), 1)

    def test_self_registration_cannot_claim_staff_entered_profile(self):
        make_member(email="walkin@example.com")
        response = self.client.post(reverse("accounts:register"), {
            "full_name": "Walk In", "username": "walkin", "email": "walkin@example.com",
            "password1": "Very-Strong-Pass-1", "password2": "Very-Strong-Pass-1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="walkin").exists())
