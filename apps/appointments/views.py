
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.memberships.forms import ReasonForm
from apps.payments.balances import balance_for
from apps.trainers.access import can_view_sessions, member_profile_for, trainer_profile_for
from apps.trainers.models import TrainerAssignment, TrainerProfile

from . import services
from .forms import SessionTypeForm, SlotSearchForm, parse_slot
from .models import Appointment, SessionType


def staff_scope(user):
    """Appointments a staff user may see: all, or (trainers) only their own."""
    qs = Appointment.objects.select_related("member", "trainer__user", "session_type")
    if user.has_perm("accounts.manage_appointments"):
        return qs
    profile = trainer_profile_for(user)
    if profile:
        return qs.filter(trainer=profile)
    if user.has_perm("accounts.view_training"):
        return qs
    return qs.none()


def can_view_staff(user):
    return can_view_sessions(user)


def can_change(user, appointment):
    """Reschedule/cancel/complete as staff: booking staff or the session's own trainer."""
    return user.has_perm("accounts.manage_appointments") or appointment.trainer.user_id == user.pk


def flash(request, ok_message=None, error=None):
    if ok_message:
        messages.success(request, ok_message)
    if error:
        messages.error(request, error)


# ---------------------------------------------------------------------------
# Staff views
# ---------------------------------------------------------------------------
class StaffMixin:
    def dispatch(self, request, *args, **kwargs):
        if not can_view_staff(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class AppointmentListView(StaffMixin, PageMetaMixin, ListView):
    template_name = "appointments/list.html"
    context_object_name = "appointments"
    paginate_by = 30
    page_title = "Training Appointments"

    def get_queryset(self):
        g = self.request.GET
        self.filters = f = {
            "scope": g.get("scope", "upcoming") if g.get("scope") in ("upcoming", "past", "all") else "upcoming",
            "status": g.get("status", "") if g.get("status") in Appointment.Status.values else "",
            "trainer": g.get("trainer", "") if g.get("trainer", "").isdigit() else "",
            "q": g.get("q", "").strip()[:100],
        }
        qs = staff_scope(self.request.user)
        now = timezone.now()
        if f["scope"] == "upcoming":
            qs = qs.filter(starts_at__gte=now).order_by("starts_at")
        elif f["scope"] == "past":
            qs = qs.filter(starts_at__lt=now).order_by("-starts_at")
        else:
            qs = qs.order_by("-starts_at")
        if f["status"]:
            qs = qs.filter(status=f["status"])
        if f["trainer"]:
            qs = qs.filter(trainer_id=f["trainer"])
        if f["q"]:
            qs = qs.filter(Q(member__full_name__icontains=f["q"]) | Q(member__member_id__iexact=f["q"]))
        return qs

    def get_context_data(self, **kwargs):
        user = self.request.user
        return super().get_context_data(
            filters=self.filters, statuses=Appointment.Status.choices,
            trainers=TrainerProfile.objects.select_related("user") if user.has_perm("accounts.manage_appointments") else [],
            can_book=user.has_perm("accounts.manage_appointments"), **kwargs,
        )


class AppointmentDetailView(StaffMixin, PageMetaMixin, DetailView):
    template_name = "appointments/detail.html"
    context_object_name = "appointment"

    def get_queryset(self):
        return staff_scope(self.request.user)

    def get_page_title(self):
        return f"{self.object.session_type.name} · {self.object.member.full_name}"

    def get_context_data(self, **kwargs):
        a = self.object
        user = self.request.user
        changeable = can_change(user, a) and a.status == Appointment.Status.BOOKED
        return super().get_context_data(
            breadcrumbs=[("Appointments", reverse("appointments:list"))],
            events=a.events.select_related("performed_by"),
            can_reschedule=changeable and not a.has_started,
            can_cancel=changeable,
            can_close=changeable and a.has_started,
            balance=balance_for(a) if a.fee else None,
            can_pay=a.fee > 0 and user.has_perm("accounts.manage_payments") and balance_for(a).payable > 0,
            can_view_payments=user.has_perm("accounts.view_payments"),
            reason_form=ReasonForm(),
            **kwargs,
        )


class BookingMixin:
    """Shared slot-search + booking flow for staff and members."""

    template_name = "appointments/book.html"
    as_member = False

    def trainer_choices(self):
        return TrainerProfile.objects.active().select_related("user")

    def fixed_member(self):
        return None

    def get_search_form(self):
        data = self.request.GET if "trainer" in self.request.GET else None
        initial = {}
        if "member" in self.request.GET and data is None:
            initial["member"] = self.request.GET.get("member")
        return SlotSearchForm(data, initial=initial, trainers=self.trainer_choices(),
                              with_member=self.fixed_member() is None)

    def get(self, request, *args, **kwargs):
        form = self.get_search_form()
        slots, chosen = [], None
        if form.is_bound and form.is_valid():
            chosen = form.cleaned_data
            slots = services.available_slots(chosen["trainer"], chosen["day"], chosen["session_type"].duration_minutes)
        return self.render(form, slots, chosen)

    def render(self, form, slots, chosen):
        from django.shortcuts import render

        return render(self.request, self.template_name, {
            "form": form, "slots": slots, "chosen": chosen, "as_member": self.as_member,
            "page_title": self.page_title, "breadcrumbs": self.crumbs(),
            "member": self.fixed_member() or (chosen or {}).get("member"),
            "horizon": settings.APPOINTMENT_BOOKING_HORIZON_DAYS,
        })

    def post(self, request, *args, **kwargs):
        form = SlotSearchForm(request.POST, trainers=self.trainer_choices(), with_member=self.fixed_member() is None)
        start = parse_slot(request.POST.get("start"))
        if not form.is_valid() or start is None:
            messages.error(request, "Please choose a trainer, session type, date and time.")
            return self.render(form, [], None)
        d = form.cleaned_data
        member = self.fixed_member() or d["member"]
        try:
            appointment = services.book(member, d["trainer"], d["session_type"], start, request.user,
                                        notes=request.POST.get("notes", ""), as_member=self.as_member, request=request)
        except services.BookingError as exc:
            messages.error(request, str(exc))
            slots = services.available_slots(d["trainer"], d["day"], d["session_type"].duration_minutes)
            return self.render(form, slots, d)
        messages.success(request, f"Booked: {services.describe(appointment)}.")
        return redirect(self.success_url(appointment))


class StaffBookView(ModulePermissionMixin, BookingMixin, View):
    permission_required = "accounts.manage_appointments"
    page_title = "Book a Training Session"

    def crumbs(self):
        return [("Appointments", reverse("appointments:list"))]

    def success_url(self, appointment):
        return appointment.get_absolute_url()


class RescheduleView(StaffMixin, PageMetaMixin, TemplateView):
    template_name = "appointments/reschedule.html"
    as_member = False

    def get_appointment(self):
        a = get_object_or_404(staff_scope(self.request.user), pk=self.kwargs["pk"])
        if not can_change(self.request.user, a):
            raise PermissionDenied
        return a

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        self.appointment = self.get_appointment()
        return super().dispatch(request, *args, **kwargs)

    def get_page_title(self):
        return "Reschedule Session"

    def get_context_data(self, **kwargs):
        a = self.appointment
        day = None
        try:
            from datetime import date
            day = date.fromisoformat(self.request.GET.get("day", ""))
        except ValueError:
            day = timezone.localtime(a.starts_at).date()
        slots = services.available_slots(a.trainer, day, a.duration_minutes, exclude=a)
        return super().get_context_data(appointment=a, day=day, slots=slots, as_member=self.as_member,
                                        today=timezone.localdate(), **kwargs)

    def post(self, request, *args, **kwargs):
        start = parse_slot(request.POST.get("start"))
        if start is None:
            flash(request, error="Choose a new time.")
            return redirect(request.path)
        try:
            services.reschedule(self.appointment, start, request.user, as_member=self.as_member, request=request)
        except services.BookingError as exc:
            flash(request, error=str(exc))
            return redirect(request.path + f"?day={timezone.localtime(start).date().isoformat()}")
        flash(request, f"Session moved to {timezone.localtime(start):%a %d %b at %H:%M}.")
        return redirect(self.done_url())

    def done_url(self):
        return self.appointment.get_absolute_url()


class _StaffActionView(StaffMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        appointment = get_object_or_404(staff_scope(request.user), pk=pk)
        if not can_change(request.user, appointment):
            raise PermissionDenied
        try:
            message = self.perform(appointment)
        except services.BookingError as exc:
            flash(request, error=str(exc))
        else:
            flash(request, message)
        return redirect(appointment.get_absolute_url())


class CancelView(_StaffActionView):
    def perform(self, appointment):
        form = ReasonForm(self.request.POST)
        reason = form.cleaned_data["reason"] if form.is_valid() else ""
        services.cancel(appointment, self.request.user, reason, request=self.request)
        return "Session cancelled. Any refund must be recorded through payments."


class CompleteView(_StaffActionView):
    def perform(self, appointment):
        attended = self.request.POST.get("outcome") != "no_show"
        services.mark_outcome(appointment, self.request.user, attended, self.request.POST.get("note", ""), self.request)
        return "Session marked as completed." if attended else "Session marked as a no-show."


# ---------------------------------------------------------------------------
# Session types (manage_trainers)
# ---------------------------------------------------------------------------
class SessionTypeListView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.manage_trainers"
    model = SessionType
    template_name = "appointments/session_types.html"
    context_object_name = "session_types"
    page_title = "Session Types"


class SessionTypeCreateView(ModulePermissionMixin, PageMetaMixin, CreateView):
    permission_required = "accounts.manage_trainers"
    form_class = SessionTypeForm
    template_name = "appointments/session_type_form.html"
    page_title = "New Session Type"

    def get_success_url(self):
        messages.success(self.request, "Session type saved.")
        return reverse("appointments:session_types")


class SessionTypeUpdateView(SessionTypeCreateView, UpdateView):
    model = SessionType
    page_title = "Edit Session Type"


# ---------------------------------------------------------------------------
# Member portal
# ---------------------------------------------------------------------------
class MemberPortalMixin:
    def dispatch(self, request, *args, **kwargs):
        self.member = member_profile_for(request.user)
        if self.member is None:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class MyAppointmentsView(MemberPortalMixin, PageMetaMixin, TemplateView):
    template_name = "appointments/my_list.html"
    page_title = "My Training Sessions"

    def get_context_data(self, **kwargs):
        qs = self.member.appointments.select_related("trainer__user", "session_type")
        now = timezone.now()
        return super().get_context_data(
            upcoming=qs.filter(starts_at__gte=now, status=Appointment.Status.BOOKED).order_by("starts_at"),
            past=qs.exclude(starts_at__gte=now, status=Appointment.Status.BOOKED).order_by("-starts_at")[:30],
            trainers=TrainerAssignment.objects.active().filter(member=self.member).select_related("trainer__user"),
            cutoff=settings.APPOINTMENT_MEMBER_CHANGE_CUTOFF_HOURS,
            **kwargs,
        )


class MyBookView(MemberPortalMixin, BookingMixin, View):
    as_member = True
    page_title = "Book a Session"

    def trainer_choices(self):
        ids = TrainerAssignment.objects.active().filter(member=self.member).values("trainer_id")
        return TrainerProfile.objects.active().filter(pk__in=ids).select_related("user")

    def fixed_member(self):
        return self.member

    def crumbs(self):
        return [("My Sessions", reverse("appointments:my_list"))]

    def success_url(self, appointment):
        return reverse("appointments:my_detail", args=[appointment.pk])


class MyAppointmentDetailView(MemberPortalMixin, PageMetaMixin, DetailView):
    template_name = "appointments/my_detail.html"
    context_object_name = "appointment"

    def get_queryset(self):
        return self.member.appointments.select_related("trainer__user", "session_type")  # ownership

    def get_page_title(self):
        return self.object.session_type.name

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            breadcrumbs=[("My Sessions", reverse("appointments:my_list"))], reason_form=ReasonForm(),
            balance=balance_for(self.object) if self.object.fee else None,
            cutoff=settings.APPOINTMENT_MEMBER_CHANGE_CUTOFF_HOURS, **kwargs,
        )


class MyRescheduleView(MemberPortalMixin, RescheduleView):
    as_member = True

    def dispatch(self, request, *args, **kwargs):
        member = member_profile_for(request.user)
        if member is None:
            raise PermissionDenied
        self.appointment = get_object_or_404(member.appointments.select_related("trainer", "session_type"),
                                             pk=kwargs["pk"])
        return TemplateView.dispatch(self, request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["breadcrumbs"] = [("My Sessions", reverse("appointments:my_list"))]
        return context

    def done_url(self):
        return reverse("appointments:my_detail", args=[self.appointment.pk])


class MyCancelView(MemberPortalMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        appointment = get_object_or_404(self.member.appointments, pk=pk)
        form = ReasonForm(request.POST)
        reason = form.cleaned_data["reason"] if form.is_valid() else ""
        try:
            services.cancel(appointment, request.user, reason, as_member=True, request=request)
        except services.BookingError as exc:
            flash(request, error=str(exc))
        else:
            flash(request, "Your session was cancelled.")
        return redirect("appointments:my_detail", pk=pk)


