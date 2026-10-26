from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role
from apps.accounts.tests.factories import make_user
from apps.nutrition.models import Meal, MealPlan, NutritionProfile
from apps.trainers.tests.factories import assign, make_active_member, make_member_with_login, make_trainer
from apps.workouts import services
from apps.workouts.models import (
    Exercise,
    MeasurementType,
    ProgressEntry,
    WorkoutDay,
    WorkoutItem,
    WorkoutLog,
    WorkoutPlan,
)


def make_exercise(name="Back Squat"):
    return Exercise.objects.create(name=name, muscle_group="legs", category="strength")


def make_template(name="Strength Basics"):
    plan = WorkoutPlan.objects.create(name=name)
    day = WorkoutDay.objects.create(plan=plan, day_number=1, title="Legs")
    WorkoutItem.objects.create(day=day, exercise=make_exercise(), sets=3, reps=10, weight_kg=Decimal("40"),
                               rest_seconds=90)
    return plan


class ExerciseLibraryTests(TestCase):
    def test_trainer_manages_library_receptionist_views(self):
        trainer = make_trainer()
        self.client.force_login(trainer.user)
        response = self.client.post(reverse("workouts:exercise_create"), {
            "name": "Plank", "muscle_group": "core", "category": "strength", "instructions": "Hold.",
            "video_url": "https://example.com/plank", "is_active": "on"})
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse("workouts:exercise_create"), {
            "name": "Bad link", "muscle_group": "core", "category": "strength", "video_url": "http://insecure.example"})
        self.assertIn("video_url", response.context["form"].errors)
        self.client.force_login(make_user(Role.RECEPTIONIST))
        self.assertContains(self.client.get(reverse("workouts:exercises")), "Plank")
        self.assertEqual(self.client.get(reverse("workouts:exercise_create")).status_code, 403)
        self.client.force_login(make_user(Role.MEMBER))
        self.assertEqual(self.client.get(reverse("workouts:exercises")).status_code, 403)


class PlanTests(TestCase):
    def setUp(self):
        self.trainer = make_trainer()
        self.member = make_active_member(full_name="Coached Member")
        self.stranger = make_active_member(full_name="Someone Else")
        assign(self.trainer, self.member)
        self.template = make_template()

    def test_build_plan_through_views(self):
        self.client.force_login(self.trainer.user)
        self.client.post(reverse("workouts:plan_create"), {"name": "Push Pull", "goal": "Strength"})
        plan = WorkoutPlan.objects.get(name="Push Pull")
        self.client.post(reverse("workouts:day_add", args=[plan.pk]), {"day_number": 1, "title": "Push"})
        day = plan.days.get()
        bench = make_exercise("Bench Press")
        self.client.post(reverse("workouts:item_add", args=[plan.pk, day.pk]),
                         {"exercise": bench.pk, "sets": 4, "reps": 8, "weight_kg": "50", "rest_seconds": 120, "order": 1})
        self.client.post(reverse("workouts:item_add", args=[plan.pk, day.pk]),
                         {"exercise": bench.pk, "order": 2})  # invalid: no sets or duration
        item = day.items.get()
        self.assertEqual(item.prescription, "4 × 8 @ 50 kg rest 120 s")
        self.client.post(reverse("workouts:item_update", args=[plan.pk, item.pk]),
                         {"exercise": bench.pk, "sets": 5, "reps": 5, "weight_kg": "60", "order": 1})
        item.refresh_from_db()
        self.assertEqual((item.sets, item.weight_kg), (5, Decimal("60")))
        self.client.post(reverse("workouts:day_add", args=[plan.pk]), {"day_number": 1, "title": "Dup"})
        self.assertEqual(plan.days.count(), 1)  # duplicate day number rejected

    def test_assign_copies_template_and_isolates_edits(self):
        self.client.force_login(self.trainer.user)
        response = self.client.post(reverse("workouts:plan_assign", args=[self.template.pk]), {
            "member": self.member.pk, "start_date": timezone.localdate().isoformat(), "end_date": ""})
        copy = WorkoutPlan.objects.get(member=self.member)
        self.assertRedirects(response, copy.get_absolute_url())
        self.assertEqual((copy.source, copy.days.count(), copy.days.get().items.count()), (self.template, 1, 1))
        WorkoutItem.objects.filter(day__plan=self.template).update(sets=9)
        self.assertEqual(copy.days.get().items.get().sets, 3)  # member copy unaffected
        self.assertTrue(copy.is_current)

    def test_trainer_cannot_coach_unassigned_member(self):
        self.client.force_login(self.trainer.user)
        self.client.post(reverse("workouts:plan_assign", args=[self.template.pk]), {
            "member": self.stranger.pk, "start_date": timezone.localdate().isoformat()})
        self.assertFalse(WorkoutPlan.objects.filter(member=self.stranger).exists())
        self.assertEqual(self.client.get(reverse("workouts:plan_create"), {"member": self.stranger.pk}).status_code, 403)
        theirs = services.copy_plan_to_member(self.template, self.stranger, timezone.localdate(), None,
                                              make_user(Role.GYM_MANAGER))
        self.assertEqual(self.client.get(theirs.get_absolute_url()).status_code, 403)
        self.assertEqual(self.client.post(reverse("workouts:day_add", args=[theirs.pk]),
                                          {"day_number": 2}).status_code, 403)

    def test_member_sees_and_logs_own_plan_only(self):
        login, me = make_member_with_login("lifter")
        mine = services.copy_plan_to_member(self.template, me, timezone.localdate(), None, make_user(Role.GYM_MANAGER))
        other = services.copy_plan_to_member(self.template, self.stranger, timezone.localdate(), None,
                                             make_user(Role.GYM_MANAGER))
        self.client.force_login(login)
        self.assertContains(self.client.get(reverse("workouts:my")), mine.name)
        page = self.client.get(mine.get_absolute_url())
        self.assertTrue(page.context["can_log"])
        self.assertFalse(page.context["editable"])  # members can't edit plans
        self.assertEqual(self.client.get(other.get_absolute_url()).status_code, 403)
        day = mine.days.get()
        item = day.items.get()
        self.client.post(reverse("workouts:log", args=[mine.pk, day.pk]), {
            "performed_on": timezone.localdate().isoformat(), "notes": "Felt strong",
            f"done_{item.pk}": "on", f"sets_{item.pk}": "3", f"reps_{item.pk}": "12", f"weight_{item.pk}": "42.5"})
        log = WorkoutLog.objects.get()
        entry = log.entries.get()
        self.assertEqual((entry.exercise_name, entry.planned, entry.reps_done), ("Back Squat", item.prescription, 12))
        # Editing the plan later doesn't rewrite the log.
        item.exercise.name = "Renamed"
        item.exercise.save()
        entry.refresh_from_db()
        self.assertEqual(entry.exercise_name, "Back Squat")
        self.assertEqual(self.client.post(reverse("workouts:log", args=[other.pk, other.days.get().pk]),
                                          {"performed_on": timezone.localdate().isoformat()}).status_code, 403)

    def test_archive_keeps_history(self):
        plan = services.copy_plan_to_member(self.template, self.member, timezone.localdate() - timedelta(days=10),
                                            None, self.trainer.user)
        self.client.force_login(self.trainer.user)
        self.client.post(reverse("workouts:plan_archive", args=[plan.pk]))
        plan.refresh_from_db()
        self.assertEqual(plan.status, "archived")
        self.assertTrue(WorkoutPlan.objects.filter(pk=plan.pk).exists())
        self.assertFalse(plan.is_current)

    def test_receptionist_sees_templates_but_not_member_plans(self):
        plan = services.copy_plan_to_member(self.template, self.member, timezone.localdate(), None, self.trainer.user)
        self.client.force_login(make_user(Role.RECEPTIONIST))
        self.assertEqual(self.client.get(self.template.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(plan.get_absolute_url()).status_code, 403)
        self.assertEqual(list(self.client.get(reverse("workouts:plans"), {"tab": "assigned"}).context["plans"]), [])


class ProgressTests(TestCase):
    def setUp(self):
        self.login, self.member = make_member_with_login("progress")
        self.trainer = make_trainer()
        self.url = reverse("workouts:progress", args=[self.member.pk])
        MeasurementType.objects.create(name="Waist", unit="cm")

    def test_member_records_own_progress_with_measurements(self):
        self.client.force_login(self.login)
        waist = MeasurementType.objects.get()
        self.client.post(self.url, {"recorded_on": (timezone.localdate() - timedelta(days=7)).isoformat(),
                                    "weight_kg": "82.5", f"m_{waist.pk}": "90"})
        self.client.post(self.url, {"recorded_on": timezone.localdate().isoformat(), "weight_kg": "81",
                                    "height_cm": "175", "notes": "Good week"})
        self.assertEqual(ProgressEntry.objects.filter(member=self.member).count(), 2)
        response = self.client.get(self.url)
        series = response.context["series"]
        self.assertEqual([p["y"] for p in series["weight"]], [82.5, 81.0])
        self.assertEqual(series["measurements"]["Waist (cm)"][0]["y"], 90.0)
        self.assertRedirects(self.client.get(reverse("workouts:my_progress")), self.url)

    def test_validation(self):
        self.client.force_login(self.login)
        for data in ({"recorded_on": timezone.localdate().isoformat(), "weight_kg": "5"},
                     {"recorded_on": (timezone.localdate() + timedelta(days=1)).isoformat(), "weight_kg": "80"},
                     {"recorded_on": timezone.localdate().isoformat()}):
            response = self.client.post(self.url, data)
            self.assertEqual(response.status_code, 200, data)
        self.assertFalse(ProgressEntry.objects.exists())

    def test_access_restricted_to_owner_assigned_trainer_and_managers(self):
        ProgressEntry.objects.create(member=self.member, weight_kg=80)
        other_login, _ = make_member_with_login("nosy")
        for user in (other_login, self.trainer.user, make_user(Role.RECEPTIONIST), make_user(Role.ACCOUNTANT)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403, user.role)
        assign(self.trainer, self.member)
        self.client.force_login(self.trainer.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.post(self.url, {"recorded_on": timezone.localdate().isoformat(), "weight_kg": "79"})
        self.assertEqual(ProgressEntry.objects.count(), 2)
        self.client.force_login(make_user(Role.GYM_MANAGER))
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_ended_assignment_revokes_trainer_access(self):
        a = assign(self.trainer, self.member)
        from apps.trainers.services import end_assignment
        end_assignment(a, make_user(Role.GYM_MANAGER))
        self.client.force_login(self.trainer.user)
        self.assertEqual(self.client.get(self.url).status_code, 403)


class NutritionTests(TestCase):
    def setUp(self):
        self.trainer = make_trainer()
        self.login, self.member = make_member_with_login("eater")
        assign(self.trainer, self.member)

    def test_meal_plan_build_and_assign(self):
        self.client.force_login(self.trainer.user)
        self.client.post(reverse("nutrition:plan_create"), {"name": "Lean Week", "goal": "Balanced"})
        plan = MealPlan.objects.get(name="Lean Week")
        self.client.post(reverse("nutrition:meal_add", args=[plan.pk]), {
            "day_number": 1, "meal_type": "breakfast", "title": "Oats", "ingredients": "Oats\nMilk\nBanana"})
        self.client.post(reverse("nutrition:meal_add", args=[plan.pk]), {"day_number": 0, "meal_type": "lunch",
                                                                         "title": "Bad day"})
        self.assertEqual(plan.meals.count(), 1)
        self.assertEqual(plan.meals.get().ingredient_list, ["Oats", "Milk", "Banana"])
        self.client.post(reverse("nutrition:plan_assign", args=[plan.pk]), {
            "member": self.member.pk, "start_date": timezone.localdate().isoformat()})
        copy = MealPlan.objects.get(member=self.member)
        self.assertEqual(copy.meals.count(), 1)

    def test_profile_privacy(self):
        url = reverse("nutrition:member", args=[self.member.pk])
        self.client.force_login(self.login)
        self.client.post(url, {"preferences": "Vegetarian", "allergies": "Peanuts", "goals": "Eat more protein"})
        self.assertEqual(NutritionProfile.objects.get(member=self.member).allergies, "Peanuts")
        self.assertContains(self.client.get(url), "not medical or dietetic advice")
        other_login, _ = make_member_with_login("other")
        self.client.force_login(other_login)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(make_user(Role.RECEPTIONIST))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.trainer.user)
        self.assertContains(self.client.get(url), "Peanuts")

    @override_settings(NUTRITION_ENABLED=False)
    def test_module_can_be_disabled(self):
        self.client.force_login(make_user(Role.SUPER_ADMIN))
        self.assertEqual(self.client.get(reverse("nutrition:plans")).status_code, 404)
        labels = {i["label"] for s in self.client.get(reverse("dashboard:home")).context["nav_sections"]
                  for i in s["items"]}
        self.assertNotIn("Meal Plans", labels)
        self.client.force_login(self.login)
        self.assertEqual(self.client.get(reverse("nutrition:my")).status_code, 404)

    def test_meal_plan_view_permissions(self):
        plan = MealPlan.objects.create(name="Private", member=self.member, start_date=timezone.localdate())
        Meal.objects.create(plan=plan, meal_type="lunch", title="Rice & curry")
        self.client.force_login(make_user(Role.RECEPTIONIST))
        self.assertEqual(self.client.get(plan.get_absolute_url()).status_code, 403)
        self.client.force_login(self.login)
        self.assertContains(self.client.get(plan.get_absolute_url()), "Rice &amp; curry")


class TrainingDashboardTests(TestCase):
    def test_training_stats_scoped_by_role(self):
        from apps.appointments import services as booking
        from apps.trainers.tests.factories import at, session_type

        t1, t2 = make_trainer(), make_trainer()
        desk = make_user(Role.RECEPTIONIST)
        st = session_type(60)
        booking.book(make_active_member(), t1, st, at(2, 10), desk)
        booking.book(make_active_member(), t2, st, at(2, 10), desk)
        booking.book(make_active_member(), t2, st, at(3, 10), desk)

        def stats(user):
            self.client.force_login(user)
            return {s["slot"].key: s["value"] for s in self.client.get(reverse("dashboard:home")).context["stats"]}

        manager = stats(make_user(Role.GYM_MANAGER))
        self.assertEqual((manager["total_trainers"], manager["upcoming_appointments"]), (2, 3))
        self.assertEqual(stats(t1.user)["upcoming_appointments"], 1)  # trainers see their own schedule
        self.assertNotIn("upcoming_appointments", stats(make_user(Role.ACCOUNTANT)))

    def test_member_portal_links(self):
        login, member = make_member_with_login("portal2")
        self.client.force_login(login)
        home = self.client.get(reverse("dashboard:home"))
        labels = {i["label"] for s in home.context["nav_sections"] for i in s["items"]}
        self.assertTrue({"My Training", "My Sessions", "My Progress", "My Nutrition"} <= labels)
        self.assertContains(home, "Book a session")
        for url in (reverse("workouts:my"), reverse("appointments:my_list"), reverse("appointments:my_book"),
                    reverse("notifications:list")):
            self.assertEqual(self.client.get(url).status_code, 200, url)
