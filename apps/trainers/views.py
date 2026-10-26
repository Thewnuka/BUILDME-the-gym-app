import mimetypes

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, FormView, ListView, UpdateView

from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.core.models import ActivityLog
from apps.core.utils import log_activity
from apps.members.models import Member

from . import services
from .access import trainer_profile_for
from .forms import (
    AssignMemberForm,
    AssignTrainerForm,
    AvailabilityForm,
    CertificateForm,
    PayRecordForm,
    TimeOffForm,
    TrainerCreateForm,
    TrainerProfileForm,
)
from .models import Availability, TimeOff, TrainerAssignment, TrainerCertificate, TrainerProfile

VIEW_PERMS = ("accounts.view_training", "accounts.manage_trainers", "accounts.manage_appointments")


def can_view_trainers(user):
    return any(user.has_perm(p) for p in VIEW_PERMS)


def trainers_crumb():
    return [("Trainers", reverse("trainers:list"))]


class TrainerViewerMixin:
    """Staff who work with trainers (any of VIEW_PERMS)."""

    def dispatch(self, request, *args, **kwargs):
        if not can_view_trainers(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class TrainerSelfOrManagerMixin:
    """The trainer themself, or staff with manage_trainers."""

    def dispatch(self, request, *args, **kwargs):
        self.trainer = get_object_or_404(TrainerProfile.objects.select_related("user"), pk=kwargs["pk"])
        self.is_self = self.trainer.user_id == request.user.pk
        if not (self.is_self or request.user.has_perm("accounts.manage_trainers")):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class TrainerListView(TrainerViewerMixin, PageMetaMixin, ListView):
    template_name = "trainers/list.html"
    context_object_name = "trainers"
    paginate_by = 24
    page_title = "Trainers"

    def get_queryset(self):
        today = timezone.localdate()
        self.q = self.request.GET.get("q", "").strip()[:100]
        self.status = self.request.GET.get("status", "")
        qs = TrainerProfile.objects.select_related("user").annotate(
            member_count=Count("assignments", distinct=True,
                               filter=Q(assignments__start_date__lte=today)
                               & (Q(assignments__end_date__isnull=True) | Q(assignments__end_date__gt=today))),
        )
        if self.q:
            qs = qs.filter(Q(user__first_name__icontains=self.q) | Q(user__last_name__icontains=self.q)
                           | Q(specialties__icontains=self.q))
        if self.status in TrainerProfile.Status.values:
            qs = qs.filter(employment_status=self.status)
        return qs.order_by("user__first_name", "user__last_name")

    def get_context_data(self, **kwargs):
        user = self.request.user
        return super().get_context_data(
            filters={"q": self.q, "status": self.status}, statuses=TrainerProfile.Status.choices,
            can_create=user.has_perm("accounts.manage_trainers") and user.has_perm("accounts.manage_users"),
            **kwargs,
        )


class TrainerDetailView(TrainerViewerMixin, PageMetaMixin, DetailView):
    model = TrainerProfile
    template_name = "trainers/detail.html"
    context_object_name = "trainer"

    def get_queryset(self):
        return TrainerProfile.objects.select_related("user")

    def get_page_title(self):
        return self.object.name

    def get_context_data(self, **kwargs):
        from apps.appointments.models import Appointment

        t = self.object
        user = self.request.user
        is_self = t.user_id == user.pk
        can_manage = user.has_perm("accounts.manage_trainers")
        can_pay = user.has_perm("accounts.view_trainer_pay")
        assignments = t.assignments.select_related("member")
        context = super().get_context_data(
            breadcrumbs=trainers_crumb(),
            is_self=is_self, can_manage=can_manage, can_edit=can_manage or is_self, can_pay=can_pay,
            can_view_progress=user.has_perm("accounts.view_member_health")
            or (is_self and user.has_perm("accounts.view_training")),
            availability=t.availability.all(),
            time_off=t.time_off.filter(end_date__gte=timezone.localdate()),
            certificates=t.certificates.all(),
            active_assignments=[a for a in assignments if a.is_active],
            past_assignments=[a for a in assignments if not a.is_active][:20],
            upcoming=Appointment.objects.upcoming().filter(trainer=t).select_related("member", "session_type")[:10],
            completed_count=Appointment.objects.filter(trainer=t, status=Appointment.Status.COMPLETED).count(),
            assign_form=AssignMemberForm() if can_manage else None,
            availability_form=AvailabilityForm(trainer=t), time_off_form=TimeOffForm(),
            certificate_form=CertificateForm(),
            **kwargs,
        )
        if can_pay:
            context["pay_records"] = t.pay_records.select_related("recorded_by")[:24]
            context["pay_form"] = PayRecordForm(initial={"period": timezone.localdate()})
            year_start = timezone.localdate().replace(month=1, day=1)
            rows = t.pay_records.filter(period__gte=year_start)
            context["pay_ytd"] = sum((r.signed_amount for r in rows), 0)
        return context


class TrainerCreateView(ModulePermissionMixin, PageMetaMixin, FormView):
    permission_required = ("accounts.manage_trainers", "accounts.manage_users")
    form_class = TrainerCreateForm
    template_name = "trainers/form.html"
    page_title = "Add Trainer"

    def get_context_data(self, **kwargs):
        return super().get_context_data(breadcrumbs=trainers_crumb(), is_create=True, **kwargs)

    def form_valid(self, form):
        try:
            profile = services.create_trainer(form, self.request.user, self.request)
        except Exception:
            form.add_error(None, "The trainer couldn't be created because the setup email failed to send. "
                                 "Check the email settings and try again.")
            return self.form_invalid(form)
        messages.success(self.request, f"Trainer {profile.name} added. A password setup link was emailed to "
                                       f"{profile.user.email}.")
        return redirect(profile.get_absolute_url())


class TrainerUpdateView(TrainerSelfOrManagerMixin, PageMetaMixin, UpdateView):
    form_class = TrainerProfileForm
    template_name = "trainers/form.html"

    def get_object(self, queryset=None):
        return self.trainer

    def get_page_title(self):
        return f"Edit {self.trainer.name}"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "staff_edit": self.request.user.has_perm("accounts.manage_trainers")}

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            breadcrumbs=trainers_crumb() + [(self.trainer.name, self.trainer.get_absolute_url())], **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        log_activity(ActivityLog.Action.TRAINER_UPDATED, f"Updated trainer profile of {self.trainer.name}",
                     request=self.request)
        messages.success(self.request, "Trainer profile saved.")
        return response


class _TrainerPostView(TrainerSelfOrManagerMixin, View):
    http_method_names = ["post"]

    def done(self, message=None, error=None):
        if message:
            messages.success(self.request, message)
        if error:
            messages.error(self.request, error)
        return redirect(self.trainer.get_absolute_url())

    def form_errors(self, form):
        errors = [e for errs in form.errors.values() for e in errs]
        return self.done(error=" ".join(errors) or "Please check the form.")


class AvailabilityAddView(_TrainerPostView):
    def post(self, request, pk):
        form = AvailabilityForm(request.POST, trainer=self.trainer)
        if not form.is_valid():
            return self.form_errors(form)
        slot = form.save(commit=False)
        slot.trainer = self.trainer
        slot.save()
        return self.done(f"Availability added: {slot}.")


class AvailabilityDeleteView(_TrainerPostView):
    def post(self, request, pk, slot_pk):
        get_object_or_404(Availability, pk=slot_pk, trainer=self.trainer).delete()
        return self.done("Availability window removed. Existing bookings are not affected.")


class TimeOffAddView(_TrainerPostView):
    def post(self, request, pk):
        form = TimeOffForm(request.POST)
        if not form.is_valid():
            return self.form_errors(form)
        item = form.save(commit=False)
        item.trainer = self.trainer
        item.save()
        return self.done(f"Time off added: {item}. Existing bookings in this period should be rescheduled.")


class TimeOffDeleteView(_TrainerPostView):
    def post(self, request, pk, item_pk):
        get_object_or_404(TimeOff, pk=item_pk, trainer=self.trainer).delete()
        return self.done("Time off removed.")


class CertificateAddView(_TrainerPostView):
    def post(self, request, pk):
        form = CertificateForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.form_errors(form)
        cert = form.save(commit=False)
        cert.trainer = self.trainer
        cert.uploaded_by = request.user
        cert.save()
        return self.done(f"Certificate '{cert.title}' added.")


class CertificateDeleteView(_TrainerPostView):
    def post(self, request, pk, cert_pk):
        get_object_or_404(TrainerCertificate, pk=cert_pk, trainer=self.trainer).delete()
        return self.done("Certificate removed.")


def _serve(field_file):
    if not field_file:
        raise Http404
    try:
        handle = field_file.open("rb")
    except FileNotFoundError:
        raise Http404
    response = FileResponse(handle, content_type=mimetypes.guess_type(field_file.name)[0] or "application/octet-stream")
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response


class CertificateFileView(TrainerViewerMixin, View):
    def get(self, request, pk, cert_pk):
        cert = get_object_or_404(TrainerCertificate, pk=cert_pk, trainer_id=pk)
        return _serve(cert.file)


class TrainerPhotoView(View):
    """Trainer photos are shown to any signed-in user (members see their trainer)."""

    def get(self, request, pk):
        return _serve(get_object_or_404(TrainerProfile, pk=pk).photo)


class AssignMemberView(ModulePermissionMixin, View):
    permission_required = "accounts.manage_trainers"
    http_method_names = ["post"]

    def post(self, request, pk):
        trainer = get_object_or_404(TrainerProfile, pk=pk)
        form = AssignMemberForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Choose an active member and a start date.")
            return redirect(trainer.get_absolute_url())
        try:
            services.assign_trainer(trainer, form.cleaned_data["member"], request.user,
                                    form.cleaned_data["start_date"], form.cleaned_data["goal"], request)
        except services.TrainerError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{form.cleaned_data['member'].full_name} assigned to {trainer.name}.")
        return redirect(trainer.get_absolute_url())


class AssignTrainerToMemberView(ModulePermissionMixin, View):
    permission_required = "accounts.manage_trainers"
    http_method_names = ["post"]

    def post(self, request, member_pk):
        member = get_object_or_404(Member, pk=member_pk)
        form = AssignTrainerForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Choose an active trainer.")
        else:
            try:
                services.assign_trainer(form.cleaned_data["trainer"], member, request.user,
                                        goal=form.cleaned_data["goal"], request=request)
            except services.TrainerError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"{form.cleaned_data['trainer'].name} is now coaching {member.full_name}.")
        return redirect(member.get_absolute_url())


class EndAssignmentView(ModulePermissionMixin, View):
    permission_required = "accounts.manage_trainers"
    http_method_names = ["post"]

    def post(self, request, assignment_pk):
        assignment = get_object_or_404(TrainerAssignment, pk=assignment_pk)
        try:
            services.end_assignment(assignment, request.user, request)
        except services.TrainerError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Assignment ended. Its history is kept.")
        target = request.POST.get("next")
        if target == "member":
            return redirect(assignment.member.get_absolute_url())
        return redirect(assignment.trainer.get_absolute_url())


class PayRecordAddView(ModulePermissionMixin, View):
    permission_required = "accounts.view_trainer_pay"
    http_method_names = ["post"]

    def post(self, request, pk):
        trainer = get_object_or_404(TrainerProfile, pk=pk)
        form = PayRecordForm(request.POST)
        if form.is_valid():
            services.record_pay(trainer, form, request.user, request)
            messages.success(request, "Pay record saved.")
        else:
            messages.error(request, "Please enter a valid date, type and amount.")
        return redirect(trainer.get_absolute_url() + "#pay")


class MyTrainerProfileView(View):
    """Shortcut for trainers to open their own profile."""

    def get(self, request):
        profile = trainer_profile_for(request.user)
        if not profile:
            raise Http404
        return redirect(profile.get_absolute_url())


