from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone


class NutritionProfile(models.Model):
    """A member's food preferences, allergies and general goals (not medical advice)."""

    member = models.OneToOneField("members.Member", on_delete=models.CASCADE, related_name="nutrition_profile")
    preferences = models.TextField("food preferences", blank=True,
                                   help_text="e.g. vegetarian, no pork, prefers rice-based meals.")
    allergies = models.TextField("allergies & intolerances", blank=True)
    goals = models.TextField("general nutrition goals", blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Nutrition profile · {self.member}"


class MealPlan(models.Model):
    """A template (no member) or a member's own copy with dates."""

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    goal = models.CharField(max_length=255, blank=True)
    member = models.ForeignKey("members.Member", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="meal_plans")
    source = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="copies")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(member__isnull=True) | Q(start_date__isnull=False),
                                   name="meal_member_plan_has_start"),
            models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(start_date__isnull=True)
                                   | Q(end_date__gte=F("start_date")), name="meal_plan_end_after_start"),
        ]

    def __str__(self):
        return f"{self.name} ({self.member.full_name})" if self.member_id else self.name

    def get_absolute_url(self):
        return reverse("nutrition:plan_detail", args=[self.pk])

    @property
    def is_template(self):
        return self.member_id is None

    @property
    def is_current(self):
        today = timezone.localdate()
        return (self.is_active and self.start_date and self.start_date <= today
                and (self.end_date is None or self.end_date >= today))


class Meal(models.Model):
    class MealType(models.TextChoices):
        BREAKFAST = "breakfast", "Breakfast"
        MORNING_SNACK = "morning_snack", "Morning snack"
        LUNCH = "lunch", "Lunch"
        AFTERNOON_SNACK = "afternoon_snack", "Afternoon snack"
        DINNER = "dinner", "Dinner"
        PRE_WORKOUT = "pre_workout", "Pre-workout"
        POST_WORKOUT = "post_workout", "Post-workout"

    ORDER = {k: i for i, k in enumerate(MealType.values)}

    plan = models.ForeignKey(MealPlan, on_delete=models.CASCADE, related_name="meals")
    day_number = models.PositiveSmallIntegerField(default=1)
    meal_type = models.CharField(max_length=16, choices=MealType.choices)
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    ingredients = models.TextField(blank=True, help_text="One ingredient per line.")

    class Meta:
        ordering = ["day_number", "pk"]
        constraints = [models.CheckConstraint(condition=Q(day_number__gte=1), name="meal_day_number_positive")]

    def __str__(self):
        return f"Day {self.day_number} · {self.get_meal_type_display()}: {self.title}"

    @property
    def ingredient_list(self):
        return [i.strip(" -•") for i in self.ingredients.splitlines() if i.strip(" -•")]
