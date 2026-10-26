from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.core.models import ActivityLog
from apps.core.utils import log_activity

from . import services
from .forms import EquipmentForm, FaultForm, MaintenanceForm
from .models import Equipment

VIEW_PERMS = ("accounts.manage_equipment", "accounts.report_equipment_faults")


class EquipmentViewerMixin:
    def dispatch(self, request, *args, **kwargs):
        if not any(request.user.has_perm(p) for p in VIEW_PERMS):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class EquipmentListView(EquipmentViewerMixin, PageMetaMixin, ListView):
    template_name = "equipment/list.html"
    context_object_name = "items"
    paginate_by = 30
    page_title = "Equipment"

    def get_queryset(self):
        g = self.request.GET
        self.filters = f = {
            "q": g.get("q", "").strip()[:100],
            "status": g.get("status", "") if g.get("status") in Equipment.Status.values else "",
            "category": g.get("category", "") if g.get("category") in Equipment.Category.values else "",
            "attention": g.get("attention") == "1",
        }
        qs = Equipment.objects.annotate(open_fault_count=Count("faults", filter=Q(faults__status="open")))
        if f["q"]:
            qs = qs.filter(Q(name__icontains=f["q"]) | Q(code__iexact=f["q"]) | Q(location__icontains=f["q"])
                           | Q(serial_number__icontains=f["q"]))
        if f["status"]:
            qs = qs.filter(status=f["status"])
        if f["category"]:
            qs = qs.filter(category=f["category"])
        if f["attention"]:
            qs = qs.filter(Q(open_fault_count__gt=0) | Q(next_maintenance_date__lte=timezone.localdate())
                           | Q(status__in=[Equipment.Status.UNDER_MAINTENANCE, Equipment.Status.OUT_OF_SERVICE]))
        return qs.order_by("code")

    def get_context_data(self, **kwargs):
        return super().get_context_data(filters=self.filters, statuses=Equipment.Status.choices,
                                        categories=Equipment.Category.choices, today=timezone.localdate(), **kwargs)


class EquipmentDetailView(EquipmentViewerMixin, PageMetaMixin, DetailView):
    model = Equipment
    template_name = "equipment/detail.html"
    context_object_name = "equipment"

    def get_page_title(self):
        return f"{self.object.code} · {self.object.name}"

    def get_context_data(self, **kwargs):
        e = self.object
        can_manage = self.request.user.has_perm("accounts.manage_equipment")
        records = e.maintenance_records.select_related("recorded_by", "expense", "supplier")
        return super().get_context_data(
            breadcrumbs=[("Equipment", reverse("equipment:list"))],
            faults=e.faults.select_related("reported_by")[:20], records=records,
            total_cost=sum((r.cost or 0) for r in records),
            can_manage=can_manage, fault_form=FaultForm(),
            maintenance_form=MaintenanceForm(equipment=e) if can_manage else None,
            statuses=Equipment.Status.choices, can_see_expenses=self.request.user.has_perm("accounts.view_expenses"),
            **kwargs)


class EquipmentCreateView(ModulePermissionMixin, PageMetaMixin, CreateView):
    permission_required = "accounts.manage_equipment"
    form_class = EquipmentForm
    template_name = "equipment/form.html"
    page_title = "Register Equipment"

    def form_valid(self, form):
        self.object = services.register_equipment(form, self.request.user, request=self.request)
        note = " Its purchase cost was recorded as an expense." if self.object.purchase_expense_id else ""
        messages.success(self.request, f"{self.object.code} registered.{note}")
        return redirect(self.object.get_absolute_url())


class EquipmentUpdateView(ModulePermissionMixin, PageMetaMixin, UpdateView):
    permission_required = "accounts.manage_equipment"
    model = Equipment
    form_class = EquipmentForm
    template_name = "equipment/form.html"
    page_title = "Edit Equipment"

    def form_valid(self, form):
        response = super().form_valid(form)
        log_activity(ActivityLog.Action.EQUIPMENT_UPDATED, f"Updated equipment {self.object.code}", request=self.request)
        messages.success(self.request, "Equipment saved.")
        return response


class ReportFaultView(EquipmentViewerMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        if not request.user.has_perm("accounts.report_equipment_faults") and \
                not request.user.has_perm("accounts.manage_equipment"):
            raise PermissionDenied
        equipment = get_object_or_404(Equipment, pk=pk)
        form = FaultForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Describe the fault and choose a severity.")
        else:
            services.report_fault(equipment, form.cleaned_data["description"], form.cleaned_data["severity"],
                                  request.user, form.cleaned_data["take_out_of_service"], request)
            messages.success(request, "Fault reported. Equipment managers have been notified.")
        return redirect(equipment.get_absolute_url())


class RecordMaintenanceView(ModulePermissionMixin, View):
    permission_required = "accounts.manage_equipment"
    http_method_names = ["post"]

    def post(self, request, pk):
        equipment = get_object_or_404(Equipment, pk=pk)
        form = MaintenanceForm(request.POST, equipment=equipment)
        if not form.is_valid():
            messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
            return redirect(equipment.get_absolute_url())
        try:
            record = services.record_maintenance(equipment, form, request.user, form.cleaned_data["status_after"],
                                                 form.cleaned_data["resolves"], request)
        except services.EquipmentError as exc:
            messages.error(request, str(exc))
        else:
            note = f" Cost recorded as expense {record.expense.reference}." if record.expense_id else ""
            messages.success(request, f"{record.get_kind_display()} recorded.{note}")
        return redirect(equipment.get_absolute_url())


class SetStatusView(ModulePermissionMixin, View):
    permission_required = "accounts.manage_equipment"
    http_method_names = ["post"]

    def post(self, request, pk):
        equipment = get_object_or_404(Equipment, pk=pk)
        try:
            services.set_status(equipment, request.POST.get("status", ""), request.user, request)
        except services.EquipmentError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Status updated.")
        return redirect(equipment.get_absolute_url())


