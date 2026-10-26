from datetime import time
from decimal import Decimal

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.trainers import services
from apps.trainers.models import Availability, CompensationRecord, TrainerAssignment, TrainerProfile

from .factories import make_active_member, make_trainer

import tempfile

MEDIA = tempfile.mkdtemp(prefix="gym-test-trainers-")


class TrainerProfileTests(TestCase):
    def test_trainer_login_gets_profile(self):
        user = make_user(Role.TRAINER)
        self.assertTrue(TrainerProfile.objects.filter(user=user).exists())
        make_user(Role.RECEPTIONIST)
        self.assertEqual(TrainerProfile.objects.count(), 1)

    def test_manager_creates_trainer_with_setup_email(self):
        self.client.force_login(make_user(Role.GYM_MANAGER))
        response = self.client.post(reverse("trainers:create"), {
            "first_name": "Nuwan", "last_name": "Fit", "username": "nuwan", "email": "nuwan@example.com",
            "phone": "0771112233", "specialties": "Strength, Mobility", "employment_type": "part_time", "hired_on": "",
        })
        user = User.objects.get(username="nuwan")
        self.assertEqual(user.role, Role.TRAINER)
        self.assertFalse(user.has_usable_password())
        self.assertRedirects(response, user.trainer_profile.get_absolute_url())
        self.assertEqual(user.trainer_profile.specialty_list, ["Strength", "Mobility"])
        self.assertEqual(len(mail.outbox), 1)

    def test_only_trainer_managers_create_trainers(self):
        for role in (Role.RECEPTIONIST, Role.TRAINER, Role.ACCOUNTANT, Role.MEMBER):
            self.client.force_login(make_user(role))
            self.assertEqual(self.client.get(reverse("trainers:create")).status_code, 403, role)

    def test_trainer_edits_own_profile_but_not_employment_details(self):
        trainer = make_trainer()
        self.client.force_login(trainer.user)
        response = self.client.get(reverse("trainers:update", args=[trainer.pk]))
        self.assertNotIn("employment_status", response.context["form"].fields)
        self.client.post(reverse("trainers:update", args=[trainer.pk]), {
            "phone": "0770000000", "specialties": "Boxing", "bio": "Hi", "qualifications": "",
            "employment_status": "inactive"})
        trainer.refresh_from_db()
        self.assertEqual(trainer.specialties, "Boxing")
        self.assertEqual(trainer.employment_status, "active")  # ignored for self-edit
        other = make_trainer()
        self.assertEqual(self.client.get(reverse("trainers:update", args=[other.pk])).status_code, 403)

    def test_availability_validation(self):
        trainer = make_trainer(all_week=False)
        self.client.force_login(trainer.user)
        url = reverse("trainers:availability_add", args=[trainer.pk])
        self.client.post(url, {"weekday": 0, "start_time": "09:00", "end_time": "12:00"})
        self.client.post(url, {"weekday": 0, "start_time": "11:00", "end_time": "13:00"})  # overlaps
        self.client.post(url, {"weekday": 1, "start_time": "15:00", "end_time": "14:00"})  # backwards
        self.assertEqual(list(Availability.objects.values_list("weekday", "start_time")), [(0, time(9))])

    @override_settings(MEDIA_ROOT=MEDIA)
    def test_certificate_upload_validates_content(self):
        trainer = make_trainer()
        self.client.force_login(trainer.user)
        url = reverse("trainers:certificate_add", args=[trainer.pk])
        fake = SimpleUploadedFile("cert.pdf", b"not really a pdf", content_type="application/pdf")
        self.client.post(url, {"title": "Fake", "file": fake})
        self.assertFalse(trainer.certificates.exists())
        real = SimpleUploadedFile("cert.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf")
        self.client.post(url, {"title": "CPR", "issuer": "Red Cross", "file": real})
        cert = trainer.certificates.get()
        self.assertNotIn("cert.pdf", cert.file.name)  # random stored name
        self.assertEqual(self.client.get(reverse("trainers:certificate_file", args=[trainer.pk, cert.pk])).status_code, 200)


class AssignmentTests(TestCase):
    def setUp(self):
        self.manager = make_user(Role.GYM_MANAGER)
        self.trainer = make_trainer()
        self.member = make_active_member()

    def test_assign_and_end_preserves_history(self):
        a = services.assign_trainer(self.trainer, self.member, self.manager)
        with self.assertRaisesMessage(services.TrainerError, "already assigned"):
            services.assign_trainer(self.trainer, self.member, self.manager)
        services.end_assignment(a, self.manager)
        a.refresh_from_db()
        self.assertIsNotNone(a.end_date)
        self.assertFalse(TrainerAssignment.objects.active().exists())
        services.assign_trainer(self.trainer, self.member, self.manager)  # re-assign allowed after ending
        self.assertEqual(TrainerAssignment.objects.count(), 2)

    def test_database_blocks_two_open_assignments(self):
        TrainerAssignment.objects.create(trainer=self.trainer, member=self.member)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TrainerAssignment.objects.create(trainer=self.trainer, member=self.member)

    def test_inactive_trainer_or_member_rejected(self):
        self.trainer.employment_status = TrainerProfile.Status.INACTIVE
        self.trainer.save()
        with self.assertRaisesMessage(services.TrainerError, "not active"):
            services.assign_trainer(self.trainer, self.member, self.manager)

    def test_assign_via_member_page_requires_permission(self):
        url = reverse("trainers:assign_to_member", args=[self.member.pk])
        self.client.force_login(make_user(Role.RECEPTIONIST))
        self.assertEqual(self.client.post(url, {"trainer": self.trainer.pk}).status_code, 403)
        self.client.force_login(self.manager)
        self.client.post(url, {"trainer": self.trainer.pk, "goal": "Lose 5 kg"})
        self.assertEqual(TrainerAssignment.objects.get().goal, "Lose 5 kg")
        response = self.client.get(self.member.get_absolute_url())
        self.assertContains(response, self.trainer.name)

    def test_trainer_sees_assigned_members_on_profile(self):
        services.assign_trainer(self.trainer, self.member, self.manager)
        self.client.force_login(self.trainer.user)
        response = self.client.get(self.trainer.get_absolute_url())
        self.assertEqual([a.member for a in response.context["active_assignments"]], [self.member])


class PayRecordTests(TestCase):
    def setUp(self):
        self.trainer = make_trainer()

    def test_pay_visible_only_to_admin_and_accountant(self):
        accountant = make_user(Role.ACCOUNTANT)
        self.client.force_login(accountant)
        self.client.post(reverse("trainers:pay_add", args=[self.trainer.pk]),
                         {"period": "2026-10-01", "kind": "salary", "amount": "85000", "notes": "Oct"})
        self.assertEqual(CompensationRecord.objects.get().amount, Decimal("85000"))
        for role in (Role.GYM_MANAGER, Role.RECEPTIONIST):
            self.client.force_login(make_user(role))
            response = self.client.get(self.trainer.get_absolute_url())
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("pay_records", response.context, role)
            self.assertNotContains(response, "85,000")
            self.assertEqual(self.client.post(reverse("trainers:pay_add", args=[self.trainer.pk]),
                                              {"period": "2026-10-01", "kind": "bonus", "amount": "1"}).status_code, 403)
        self.client.force_login(self.trainer.user)  # trainers can't see their own pay records here either
        self.assertNotContains(self.client.get(self.trainer.get_absolute_url()), "85,000")
        self.client.force_login(make_user(Role.SUPER_ADMIN))
        self.assertContains(self.client.get(self.trainer.get_absolute_url()), "85,000")
        self.assertEqual(CompensationRecord.objects.count(), 1)

    def test_pay_amount_not_written_to_activity_log(self):
        from apps.core.models import ActivityLog
        self.client.force_login(make_user(Role.ACCOUNTANT))
        self.client.post(reverse("trainers:pay_add", args=[self.trainer.pk]),
                         {"period": "2026-10-01", "kind": "commission", "amount": "12345"})
        log = ActivityLog.objects.get(action=ActivityLog.Action.TRAINER_PAY_RECORDED)
        self.assertNotIn("12345", log.description)
