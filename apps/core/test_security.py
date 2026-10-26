"""Security regressions found in the final review: credential exposure, direct file access, dead-end links."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import identify_hasher
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.appointments.models import Appointment
from apps.memberships.tests.factories import make_member, make_membership, make_plan
from apps.payments import services as pay
from apps.trainers.models import TrainerAssignment
from apps.trainers.tests.factories import at, make_member_with_login, make_trainer, session_type


class CheckInPassTests(TestCase):
    """A QR pass works as an entry credential: only the front desk and the member may open it."""

    def test_pass_access(self):
        login, member = make_member_with_login("passholder")
        url = reverse("attendance:pass", args=[member.pk])
        for role, expected in ((Role.RECEPTIONIST, 200), (Role.GYM_MANAGER, 200), (Role.SUPER_ADMIN, 200),
                               (Role.TRAINER, 403), (Role.ACCOUNTANT, 403)):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(url).status_code, expected, role)
        self.client.force_login(login)
        self.assertEqual(self.client.get(url).status_code, 200)
        other, _ = make_member_with_login("otherpass")
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_member_page_hides_pass_link_from_trainers(self):
        _, member = make_member_with_login("passlink")
        self.client.force_login(make_user(Role.TRAINER))
        self.assertNotContains(self.client.get(member.get_absolute_url()), reverse("attendance:pass", args=[member.pk]))


@override_settings(DEBUG=True)
class MediaNotPublicTests(TestCase):
    def test_uploads_are_not_served_from_media_url(self):
        self.client.force_login(make_user(Role.MEMBER))
        self.assertEqual(self.client.get("/media/members/photos/anything.jpg").status_code, 404)


class NoDeadEndLinkTests(TestCase):
    """Pages never link to pages the viewer would be refused (HTTP 403)."""

    def test_trainer_sees_member_without_membership_links(self):
        member = make_member()
        ms = make_membership(member, make_plan(), timezone.localdate())
        self.client.force_login(make_user(Role.TRAINER))
        response = self.client.get(member.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, ms.get_absolute_url())
        self.assertEqual(self.client.get(ms.get_absolute_url()).status_code, 403)

    def test_accountant_session_payment_without_appointment_links(self):
        trainer = make_trainer("paylinks")
        member = make_member()
        make_membership(member, make_plan(), timezone.localdate())
        appt = Appointment.objects.create(member=member, trainer=trainer, session_type=session_type(60, "3000"),
                                          starts_at=at(1, 10), ends_at=at(1, 11), duration_minutes=60, fee=Decimal("3000"))
        accountant = make_user(Role.ACCOUNTANT)
        payment, _ = pay.record_payment(appt, Decimal("1000"), "cash", timezone.localdate(), accountant)
        self.client.force_login(accountant)
        for url in (payment.get_absolute_url(), reverse("payments:outstanding"),
                    reverse("payments:record_session", args=[appt.pk])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertNotContains(response, appt.get_absolute_url())
            self.assertNotContains(response, f'href="{reverse("appointments:list")}"')

    def test_receptionist_trainer_page_without_progress_links(self):
        trainer = make_trainer("progresslinks")
        member = make_member()
        TrainerAssignment.objects.create(trainer=trainer, member=member, start_date=timezone.localdate())
        progress = reverse("workouts:progress", args=[member.pk])
        self.client.force_login(make_user(Role.RECEPTIONIST))
        self.assertNotContains(self.client.get(trainer.get_absolute_url()), progress)
        self.client.force_login(trainer.user)  # the coach does get the link
        self.assertContains(self.client.get(trainer.get_absolute_url()), progress)


class PasswordStorageTests(TestCase):
    def test_passwords_are_hashed_never_stored_plain(self):
        self.client.post(reverse("accounts:register"), {
            "full_name": "Hash Check", "username": "hashcheck", "email": "hash@example.com",
            "password1": "Strong-Pass-2026!", "password2": "Strong-Pass-2026!"})
        from apps.accounts.models import User

        user = User.objects.get(username="hashcheck")
        self.assertNotIn("Strong-Pass-2026!", user.password)
        self.assertTrue(identify_hasher(user.password))
        self.assertTrue(user.check_password("Strong-Pass-2026!"))


class UploadSecurityTests(TestCase):
    def test_disguised_files_rejected_everywhere(self):
        manager = make_user(Role.GYM_MANAGER)
        self.client.force_login(manager)
        fake_png = SimpleUploadedFile("photo.png", b"<?php system($_GET['c']); ?>", content_type="image/png")
        svg = SimpleUploadedFile("photo.svg", b"<svg onload=alert(1)>", content_type="image/svg+xml")
        for upload in (fake_png, svg):
            upload.seek(0)
            self.client.post(reverse("members:create"), {
                "full_name": "Upload Test", "phone": "0770000123", "joined_on": timezone.localdate().isoformat(),
                "photo": upload})
        from apps.members.models import Member

        self.assertFalse(Member.objects.filter(full_name="Upload Test").exists())
        self.client.post(reverse("workouts:exercise_create"), {
            "name": "Bad image", "muscle_group": "legs", "category": "strength",
            "image": SimpleUploadedFile("x.jpg", b"GIF89a not really", content_type="image/jpeg")})
        from apps.workouts.models import Exercise

        self.assertFalse(Exercise.objects.filter(name="Bad image").exists())

    def test_expired_assignment_removes_trainer_access(self):
        trainer = make_trainer("expiredcoach")
        member = make_member()
        a = TrainerAssignment.objects.create(trainer=trainer, member=member,
                                             start_date=timezone.localdate() - timedelta(days=30))
        self.client.force_login(trainer.user)
        url = reverse("workouts:progress", args=[member.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        TrainerAssignment.objects.filter(pk=a.pk).update(end_date=timezone.localdate())
        self.assertEqual(self.client.get(url).status_code, 403)
