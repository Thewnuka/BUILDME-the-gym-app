from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.members.models import Member
from apps.trainers.access import (
    can_coach,
    can_view_member_health,
    coached_members,
    member_profile_for,
    trainer_profile_for,
)
from apps.trainers.models import TrainerAssignment

from . import services
from .forms import AssignPlanForm, DayForm, ExerciseForm, ItemForm, MeasurementTypeForm, PlanForm, ProgressForm
from .models import Exercise, MeasurementType, ProgressEntry, WorkoutDay, WorkoutItem, WorkoutLog, WorkoutPlan


def can_view_library(user):
    return user.has_perm("accounts.view_training") or user.has_perm("accounts.manage_training")


def can_view_plan(user, plan):
    return can_view_library(user) if plan.is_template else can_view_member_health(user, plan.member)


def can_edit_plan(user, plan):
    if plan.status == WorkoutPlan.Status.ARCHIVED:
        return False
    return user.has_perm("accounts.manage_training") if plan.is_template else can_coach(user, plan.member)


def visible_member_plans(user):
    """Assigned plans whose member's health data ``user`` may see."""
    qs = WorkoutPlan.objects.assigned().select_related("member")
    if user.has_perm("accounts.view_member_health"):
        return qs
    profile = trainer_profile_for(user)
    if profile and user.has_perm("accounts.view_training"):
        member_ids = TrainerAssignment.objects.active().filter(trainer=profile).values("member_id")
        return qs.filter(member_id__in=member_ids)
    return qs.none()


class LibraryMixin:
    def dispatch(self, request, *args, **kwargs):
        if not can_view_library(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Exercise library
# ---------------------------------------------------------------------------
class ExerciseListView(LibraryMixin, PageMetaMixin, ListView):
    template_name = "workouts/exercise_list.html"
    context_object_name = "exercises"
    paginate_by = 30
    page_title = "Exercise Library"

    def get_queryset(self):
        g = self.request.GET
        self.filters = {
            "q": g.get("q", "").strip()[:100],
            "muscle": g.get("muscle", "") if g.get("muscle") in Exercise.MuscleGroup.values else "",
            "category": g.get("category", "") if g.get("category") in Exercise.Category.values else "",
        }
        qs = Exercise.objects.all()
        if not self.request.user.has_perm("accounts.manage_training"):
            qs = qs.filter(is_active=True)
        if self.filters["q"]:
            qs = qs.filter(Q(name__icontains=self.filters["q"]) | Q(description__icontains=self.filters["q"]))
        if self.filters["muscle"]:
            qs = qs.filter(muscle_group=self.filters["muscle"])
        if self.filters["category"]:
            qs = qs.filter(category=self.filters["category"])
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(filters=self.filters, muscles=Exercise.MuscleGroup.choices,
                                        categories=Exercise.Category.choices,
                                        is_filtered=any(self.filters.values()), **kwargs)


class ExerciseDetailView(PageMetaMixin, DetailView):
    """Library users see any exercise; anyone else only exercises in a workout plan they may view."""

    model = Exercise
    template_name = "workouts/exercise_detail.html"
    context_object_name = "exercise"

    def get_object(self, queryset=None):
        exercise = super().get_object(queryset)
        user = self.request.user
        if can_view_library(user):
            return exercise
        plans = WorkoutPlan.objects.filter(days__items__exercise=exercise, member__isnull=False).select_related("member")
        if any(can_view_member_health(user, plan.member) for plan in plans.distinct()):
            return exercise
        raise PermissionDenied

    def get_page_title(self):
        return self.object.name

    def get_context_data(self, **kwargs):
        crumbs = [("Exercise Library", reverse("workouts:exercises"))] if can_view_library(self.request.user) else []
        return super().get_context_data(breadcrumbs=crumbs, **kwargs)


class ExerciseCreateView(ModulePermissionMixin, PageMetaMixin, CreateView):
    permission_required = "accounts.manage_training"
    form_class = ExerciseForm
    template_name = "workouts/exercise_form.html"
    page_title = "New Exercise"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, "Exercise saved.")
        return super().form_valid(form)


class ExerciseUpdateView(ModulePermissionMixin, PageMetaMixin, UpdateView):
    permission_required = "accounts.manage_training"
    model = Exercise
    form_class = ExerciseForm
    template_name = "workouts/exercise_form.html"
    page_title = "Edit Exercise"

    def form_valid(self, form):
        messages.success(self.request, "Exercise saved.")
        return super().form_valid(form)


class ExerciseImageView(View):
    """Exercise images are shown to any signed-in user (members see them in their plans)."""

    def get(self, request, pk):
        exercise = get_object_or_404(Exercise, pk=pk)
        if not exercise.image:
            raise Http404
        return FileResponse(exercise.image.open("rb"))


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
class PlanListView(LibraryMixin, PageMetaMixin, ListView):
    template_name = "workouts/plan_list.html"
    context_object_name = "plans"
    paginate_by = 30
    page_title = "Workout Plans"

    def get_queryset(self):
        self.tab = "assigned" if self.request.GET.get("tab") == "assigned" else "templates"
        self.q = self.request.GET.get("q", "").strip()[:100]
        if self.tab == "assigned":
            qs = visible_member_plans(self.request.user)
            if self.q:
                qs = qs.filter(Q(name__icontains=self.q) | Q(member__full_name__icontains=self.q)
                               | Q(member__member_id__iexact=self.q))
            return qs.order_by("status", "-start_date")
        qs = WorkoutPlan.objects.templates().annotate(day_count=Count("days"))
        if self.q:
            qs = qs.filter(name__icontains=self.q)
        return qs.order_by("name")

    def get_context_data(self, **kwargs):
        return super().get_context_data(tab=self.tab, q=self.q, **kwargs)


class PlanCreateView(ModulePermissionMixin, PageMetaMixin, CreateView):
    """New template, or (``?member=``) a plan written directly for one member."""

    permission_required = "accounts.manage_training"
    form_class = PlanForm
    template_name = "workouts/plan_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.member = None
        member_id = request.GET.get("member") or request.POST.get("member")
        if member_id and str(member_id).isdigit():
            self.member = get_object_or_404(Member, pk=member_id)
            if not can_coach(request.user, self.member):
                raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_page_title(self):
        return f"New Workout Plan · {self.member.full_name}" if self.member else "New Workout Plan Template"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "for_member": self.member is not None}

    def get_context_data(self, **kwargs):
        return super().get_context_data(member=self.member, **kwargs)

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.member = self.member
        messages.success(self.request, "Plan created. Add days and exercises below.")
        return super().form_valid(form)


class PlanUpdateView(PageMetaMixin, UpdateView):
    model = WorkoutPlan
    form_class = PlanForm
    template_name = "workouts/plan_form.html"

    def get_object(self, queryset=None):
        plan = super().get_object(queryset)
        if not can_edit_plan(self.request.user, plan):
            raise PermissionDenied
        return plan

    def get_page_title(self):
        return f"Edit {self.object.name}"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "for_member": not self.object.is_template}

    def get_context_data(self, **kwargs):
        return super().get_context_data(member=self.object.member, **kwargs)


class PlanDetailView(PageMetaMixin, DetailView):
    template_name = "workouts/plan_detail.html"
    context_object_name = "plan"

    def get_queryset(self):
        return WorkoutPlan.objects.select_related("member", "source", "created_by")

    def get_object(self, queryset=None):
        plan = super().get_object(queryset)
        if not can_view_plan(self.request.user, plan):
            raise PermissionDenied
        return plan

    def get_page_title(self):
        return self.object.name

    def get_context_data(self, **kwargs):
        plan = self.object
        user = self.request.user
        editable = can_edit_plan(user, plan)
        is_owner = plan.member_id and plan.member.user_id == user.pk
        context = super().get_context_data(
            days=plan.days.prefetch_related("items__exercise"),
            editable=editable,
            can_log=(not plan.is_template and plan.status == WorkoutPlan.Status.ACTIVE
                     and (is_owner or can_coach(user, plan.member))),
            day_form=DayForm(plan=plan), item_form=ItemForm(),
            logs=plan.logs.prefetch_related("entries")[:10] if not plan.is_template else [],
            **kwargs,
        )
        if plan.is_template and user.has_perm("accounts.manage_training"):
            context["assign_form"] = AssignPlanForm(members=coached_members(user))
        if plan.member_id and not is_owner:
            context["breadcrumbs"] = [("Members", reverse("members:list")),
                                      (plan.member.full_name, plan.member.get_absolute_url())]
        elif is_owner:
            context["breadcrumbs"] = [("My Training", reverse("workouts:my"))]
        else:
            context["breadcrumbs"] = [("Workout Plans", reverse("workouts:plans"))]
        return context


class _PlanEditView(View):
    """POST-only edits to a plan's days/items, guarded by can_edit_plan."""

    http_method_names = ["post"]

    def post(self, request, pk, **kwargs):
        self.plan = get_object_or_404(WorkoutPlan, pk=pk)
        if not can_edit_plan(request.user, self.plan):
            raise PermissionDenied
        message = self.perform(**kwargs)
        if message:
            (messages.success if not message.startswith("!") else messages.error)(request, message.lstrip("!"))
        return redirect(self.plan.get_absolute_url())

    @staticmethod
    def errors(form):
        return "!" + " ".join(e for errs in form.errors.values() for e in errs)


class AddDayView(_PlanEditView):
    def perform(self):
        form = DayForm(self.request.POST, plan=self.plan)
        if not form.is_valid():
            return self.errors(form)
        day = form.save(commit=False)
        day.plan = self.plan
        day.save()
        return f"{day} added."


class DeleteDayView(_PlanEditView):
    def perform(self, day_pk):
        get_object_or_404(WorkoutDay, pk=day_pk, plan=self.plan).delete()
        return "Day removed. Logged workouts keep their own record."


class AddItemView(_PlanEditView):
    def perform(self, day_pk):
        day = get_object_or_404(WorkoutDay, pk=day_pk, plan=self.plan)
        form = ItemForm(self.request.POST)
        if not form.is_valid():
            return self.errors(form)
        item = form.save(commit=False)
        item.day = day
        item.save()
        return f"{item.exercise.name} added to {day}."


class UpdateItemView(_PlanEditView):
    def perform(self, item_pk):
        item = get_object_or_404(WorkoutItem, pk=item_pk, day__plan=self.plan)
        form = ItemForm(self.request.POST, instance=item)
        if not form.is_valid():
            return self.errors(form)
        form.save()
        return f"{item.exercise.name} updated."


class DeleteItemView(_PlanEditView):
    def perform(self, item_pk):
        get_object_or_404(WorkoutItem, pk=item_pk, day__plan=self.plan).delete()
        return "Exercise removed from the plan."


class ArchivePlanView(_PlanEditView):
    def perform(self):
        if self.plan.is_template:
            return "!Templates can't be archived."
        self.plan.status = WorkoutPlan.Status.ARCHIVED
        if not self.plan.end_date or self.plan.end_date > timezone.localdate():
            self.plan.end_date = max(timezone.localdate(), self.plan.start_date)
        self.plan.save(update_fields=["status", "end_date", "updated_at"])
        return "Plan archived. It stays in the member's history."


class AssignPlanView(ModulePermissionMixin, View):
    permission_required = "accounts.manage_training"
    http_method_names = ["post"]

    def post(self, request, pk):
        template = get_object_or_404(WorkoutPlan.objects.templates(), pk=pk)
        form = AssignPlanForm(request.POST, members=coached_members(request.user))
        if not form.is_valid():
            messages.error(request, "Choose one of your members and valid dates.")
            return redirect(template.get_absolute_url())
        member = form.cleaned_data["member"]
        if not can_coach(request.user, member):
            raise PermissionDenied
        plan = services.copy_plan_to_member(template, member, form.cleaned_data["start_date"],
                                            form.cleaned_data["end_date"], request.user, request)
        messages.success(request, f"'{plan.name}' assigned to {member.full_name}. You can tailor their copy here.")
        return redirect(plan.get_absolute_url())


class LogWorkoutView(PageMetaMixin, TemplateView):
    template_name = "workouts/log_form.html"
    page_title = "Log Workout"

    def dispatch(self, request, *args, **kwargs):
        self.plan = get_object_or_404(WorkoutPlan.objects.assigned().select_related("member"), pk=kwargs["pk"])
        is_owner = self.plan.member.user_id == request.user.pk
        if not (is_owner or can_coach(request.user, self.plan.member)):
            raise PermissionDenied
        if self.plan.status != WorkoutPlan.Status.ACTIVE:
            raise PermissionDenied
        self.day = get_object_or_404(self.plan.days.prefetch_related("items__exercise"), pk=kwargs["day_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        return super().get_context_data(plan=self.plan, day=self.day, items=self.day.items.all(),
                                        today=timezone.localdate(), **kwargs)

    def post(self, request, *args, **kwargs):
        def number(name, cast=int):
            value = request.POST.get(name, "").strip()
            try:
                result = cast(value) if value else None
            except (ValueError, ArithmeticError):
                return None
            return result if result is None or result >= 0 else None

        try:
            performed_on = date.fromisoformat(request.POST.get("performed_on", ""))
        except ValueError:
            performed_on = timezone.localdate()
        if performed_on > timezone.localdate():
            messages.error(request, "A workout can't be logged in the future.")
            return redirect(request.path)
        from decimal import Decimal

        rows = [(item, f"done_{item.pk}" in request.POST, number(f"sets_{item.pk}"), number(f"reps_{item.pk}"),
                 number(f"weight_{item.pk}", Decimal)) for item in self.day.items.all()]
        services.log_workout(self.plan, self.day, performed_on, rows, request.POST.get("notes", ""), request.user)
        messages.success(request, f"Workout logged for {performed_on:%d %b}. Great work!")
        return redirect(self.plan.get_absolute_url())


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------
class ProgressView(PageMetaMixin, TemplateView):
    template_name = "workouts/progress.html"

    def dispatch(self, request, *args, **kwargs):
        self.member = get_object_or_404(Member, pk=kwargs["member_pk"])
        if not can_view_member_health(request.user, self.member):
            raise PermissionDenied
        self.is_owner = self.member.user_id == request.user.pk
        self.can_record = self.is_owner or can_coach(request.user, self.member)
        return super().dispatch(request, *args, **kwargs)

    def get_page_title(self):
        return "My Progress" if self.is_owner else f"Progress · {self.member.full_name}"

    def get_context_data(self, form=None, **kwargs):
        entries = ProgressEntry.objects.filter(member=self.member).prefetch_related("measurements__measurement_type")
        crumbs = [("My Training", reverse("workouts:my"))] if self.is_owner else \
            [("Members", reverse("members:list")), (self.member.full_name, self.member.get_absolute_url())]
        return super().get_context_data(
            member=self.member, entries=entries[:50], is_owner=self.is_owner, can_record=self.can_record,
            form=form or (ProgressForm(initial={"recorded_on": timezone.localdate()}) if self.can_record else None),
            series=services.progress_series(self.member), breadcrumbs=crumbs, **kwargs,
        )

    def post(self, request, *args, **kwargs):
        if not self.can_record:
            raise PermissionDenied
        form = ProgressForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        services.record_progress(self.member, form, request.user)
        messages.success(request, "Progress recorded.")
        return redirect(request.path)


class MeasurementTypeView(ModulePermissionMixin, PageMetaMixin, TemplateView):
    permission_required = "accounts.manage_training"
    template_name = "workouts/measurement_types.html"
    page_title = "Measurement Types"

    def get_context_data(self, form=None, **kwargs):
        return super().get_context_data(types=MeasurementType.objects.all(), form=form or MeasurementTypeForm(), **kwargs)

    def post(self, request):
        if "toggle" in request.POST:
            mt = get_object_or_404(MeasurementType, pk=request.POST["toggle"])
            mt.is_active = not mt.is_active
            mt.save(update_fields=["is_active"])
            messages.success(request, f"'{mt.name}' {'enabled' if mt.is_active else 'hidden'}.")
            return redirect(request.path)
        form = MeasurementTypeForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        form.save()
        messages.success(request, "Measurement type added.")
        return redirect(request.path)


# ---------------------------------------------------------------------------
# Member portal
# ---------------------------------------------------------------------------
class MyTrainingView(PageMetaMixin, TemplateView):
    """Member hub: trainers, sessions, workout plans and latest progress."""

    template_name = "workouts/my_training.html"
    page_title = "My Training"

    def dispatch(self, request, *args, **kwargs):
        self.member = member_profile_for(request.user)
        if self.member is None:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from apps.appointments.models import Appointment

        plans = self.member.workout_plans.all()
        return super().get_context_data(
            member=self.member,
            trainers=TrainerAssignment.objects.active().filter(member=self.member).select_related("trainer__user"),
            upcoming=Appointment.objects.upcoming().filter(member=self.member).select_related("trainer__user",
                                                                                               "session_type")[:5],
            current_plans=[p for p in plans if p.is_current],
            other_plans=[p for p in plans if not p.is_current][:10],
            recent_logs=WorkoutLog.objects.filter(member=self.member)[:5],
            latest_progress=ProgressEntry.objects.filter(member=self.member).first(),
            **kwargs,
        )


class MyProgressView(View):
    def get(self, request):
        member = member_profile_for(request.user)
        if member is None:
            raise PermissionDenied
        return redirect("workouts:progress", member_pk=member.pk)
