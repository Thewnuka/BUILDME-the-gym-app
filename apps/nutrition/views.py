"""
Optional nutrition module. Disable with NUTRITION_ENABLED=False (pages 404, menu hidden).
Meal plans and profiles are general guidance recorded by staff, not medical or
dietetic advice; nothing here calculates or diagnoses anything.
"""
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView

from apps.core.models import ActivityLog
from apps.core.utils import log_activity
from apps.members.models import Member
from apps.trainers.access import can_coach, can_view_member_health, coached_members, member_profile_for
from apps.workouts.forms import AssignPlanForm

from .forms import MealForm, MealPlanForm, NutritionProfileForm
from .models import Meal, MealPlan, NutritionProfile


class EnabledMixin:
    def dispatch(self, request, *args, **kwargs):
        if not settings.NUTRITION_ENABLED:
            raise Http404("The nutrition module is disabled.")
        return super().dispatch(request, *args, **kwargs)


def can_view_library(user):
    return user.has_perm("accounts.view_training") or user.has_perm("accounts.manage_training")


def can_view_plan(user, plan):
    return can_view_library(user) if plan.is_template else can_view_member_health(user, plan.member)


def can_edit_plan(user, plan):
    return user.has_perm("accounts.manage_training") if plan.is_template else can_coach(user, plan.member)


class MealPlanListView(EnabledMixin, ListView):
    template_name = "nutrition/plan_list.html"
    context_object_name = "plans"
    paginate_by = 30

    def dispatch(self, request, *args, **kwargs):
        if not can_view_library(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return MealPlan.objects.filter(member__isnull=True).annotate(meal_count=Count("meals")).order_by("name")

    def get_context_data(self, **kwargs):
        return super().get_context_data(page_title="Meal Plans", **kwargs)


class MealPlanCreateView(EnabledMixin, CreateView):
    form_class = MealPlanForm
    template_name = "nutrition/plan_form.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.has_perm("accounts.manage_training"):
            raise PermissionDenied
        self.member = None
        member_id = request.GET.get("member") or request.POST.get("member")
        if member_id and str(member_id).isdigit():
            self.member = get_object_or_404(Member, pk=member_id)
            if not can_coach(request.user, self.member):
                raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "for_member": self.member is not None}

    def get_context_data(self, **kwargs):
        title = f"New Meal Plan · {self.member.full_name}" if self.member else "New Meal Plan Template"
        return super().get_context_data(page_title=title, member=self.member, **kwargs)

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.member = self.member
        messages.success(self.request, "Meal plan created. Add meals below.")
        return super().form_valid(form)


class MealPlanDetailView(EnabledMixin, DetailView):
    template_name = "nutrition/plan_detail.html"
    context_object_name = "plan"

    def get_queryset(self):
        return MealPlan.objects.select_related("member")

    def get_object(self, queryset=None):
        plan = super().get_object(queryset)
        if not can_view_plan(self.request.user, plan):
            raise PermissionDenied
        return plan

    def get_context_data(self, **kwargs):
        plan = self.object
        user = self.request.user
        days = {}
        for meal in plan.meals.all():
            days.setdefault(meal.day_number, []).append(meal)
        for meals in days.values():
            meals.sort(key=lambda m: Meal.ORDER.get(m.meal_type, 99))
        context = super().get_context_data(
            page_title=plan.name, days=sorted(days.items()), editable=can_edit_plan(user, plan),
            meal_form=MealForm(initial={"day_number": 1}), **kwargs,
        )
        if plan.is_template and user.has_perm("accounts.manage_training"):
            context["assign_form"] = AssignPlanForm(members=coached_members(user))
        if plan.member_id:
            context["breadcrumbs"] = [("Nutrition", reverse("nutrition:member", args=[plan.member_id]))]
        else:
            context["breadcrumbs"] = [("Meal Plans", reverse("nutrition:plans"))]
        return context


class _MealPlanEdit(EnabledMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, **kwargs):
        self.plan = get_object_or_404(MealPlan, pk=pk)
        if not can_edit_plan(request.user, self.plan):
            raise PermissionDenied
        self.perform(**kwargs)
        return redirect(self.plan.get_absolute_url())


class AddMealView(_MealPlanEdit):
    def perform(self):
        form = MealForm(self.request.POST)
        if form.is_valid():
            meal = form.save(commit=False)
            meal.plan = self.plan
            meal.save()
            messages.success(self.request, f"{meal.get_meal_type_display()} added to day {meal.day_number}.")
        else:
            messages.error(self.request, " ".join(e for errs in form.errors.values() for e in errs))


class DeleteMealView(_MealPlanEdit):
    def perform(self, meal_pk):
        get_object_or_404(Meal, pk=meal_pk, plan=self.plan).delete()
        messages.success(self.request, "Meal removed.")


class AssignMealPlanView(EnabledMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        if not request.user.has_perm("accounts.manage_training"):
            raise PermissionDenied
        template = get_object_or_404(MealPlan, pk=pk, member__isnull=True)
        form = AssignPlanForm(request.POST, members=coached_members(request.user))
        if not form.is_valid() or not can_coach(request.user, form.cleaned_data["member"]):
            messages.error(request, "Choose one of your members and valid dates.")
            return redirect(template.get_absolute_url())
        member = form.cleaned_data["member"]
        with transaction.atomic():
            plan = MealPlan.objects.create(name=template.name, description=template.description, goal=template.goal,
                                           member=member, source=template, start_date=form.cleaned_data["start_date"],
                                           end_date=form.cleaned_data["end_date"], created_by=request.user)
            Meal.objects.bulk_create([Meal(plan=plan, day_number=m.day_number, meal_type=m.meal_type, title=m.title,
                                           description=m.description, ingredients=m.ingredients)
                                      for m in template.meals.all()])
        log_activity(ActivityLog.Action.MEAL_PLAN_ASSIGNED, f"Assigned meal plan '{plan.name}' to {member.member_id}",
                     request=request)
        messages.success(request, f"Meal plan assigned to {member.full_name}.")
        return redirect(plan.get_absolute_url())


class MemberNutritionView(EnabledMixin, TemplateView):
    """A member's nutrition profile and meal plans (member themself, assigned trainers, health staff)."""

    template_name = "nutrition/member.html"

    def dispatch(self, request, *args, **kwargs):
        self.member = get_object_or_404(Member, pk=kwargs["member_pk"])
        if not can_view_member_health(request.user, self.member):
            raise PermissionDenied
        self.is_owner = self.member.user_id == request.user.pk
        self.can_edit_profile = self.is_owner or can_coach(request.user, self.member)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, form=None, **kwargs):
        profile = NutritionProfile.objects.filter(member=self.member).first()
        crumbs = [("My Training", reverse("workouts:my"))] if self.is_owner else \
            [("Members", reverse("members:list")), (self.member.full_name, self.member.get_absolute_url())]
        return super().get_context_data(
            page_title="My Nutrition" if self.is_owner else f"Nutrition · {self.member.full_name}",
            member=self.member, profile=profile, is_owner=self.is_owner, can_edit_profile=self.can_edit_profile,
            can_coach=can_coach(self.request.user, self.member),
            form=form or (NutritionProfileForm(instance=profile) if self.can_edit_profile else None),
            plans=self.member.meal_plans.all(), breadcrumbs=crumbs, **kwargs,
        )

    def post(self, request, *args, **kwargs):
        if not self.can_edit_profile:
            raise PermissionDenied
        profile = NutritionProfile.objects.filter(member=self.member).first()
        form = NutritionProfileForm(request.POST, instance=profile)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        obj = form.save(commit=False)
        obj.member = self.member
        obj.updated_by = request.user
        obj.save()
        messages.success(request, "Nutrition profile saved.")
        return redirect(request.path)


class MyNutritionView(EnabledMixin, View):
    def get(self, request):
        member = member_profile_for(request.user)
        if member is None:
            raise PermissionDenied
        return redirect("nutrition:member", member_pk=member.pk)
