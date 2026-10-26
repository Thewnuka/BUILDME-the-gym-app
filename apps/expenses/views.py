import mimetypes
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.core.models import ActivityLog
from apps.core.utils import log_activity
from apps.memberships.forms import ReasonForm

from . import services
from .forms import ExpenseForm, SupplierForm
from .models import Expense, ExpenseCategory, Supplier

MONEY = DecimalField(max_digits=14, decimal_places=2)
SUPPLIER_PERMS = ("accounts.manage_expenses", "accounts.manage_inventory", "accounts.manage_equipment")


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


class ExpenseListView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.view_expenses"
    template_name = "expenses/list.html"
    context_object_name = "expenses"
    paginate_by = 30
    page_title = "Expenses"

    def get_queryset(self):
        g = self.request.GET
        self.filters = f = {
            "q": g.get("q", "").strip()[:100],
            "category": g.get("category", "") if g.get("category") in ExpenseCategory.values else "",
            "supplier": g.get("supplier", "") if g.get("supplier", "").isdigit() else "",
            "status": g.get("status", "active") if g.get("status", "active") in ("active", "voided", "all") else "active",
            "date_from": _parse_date(g.get("date_from")),
            "date_to": _parse_date(g.get("date_to")),
        }
        qs = Expense.objects.select_related("supplier", "recorded_by")
        if f["q"]:
            qs = qs.filter(Q(description__icontains=f["q"]) | Q(reference__iexact=f["q"])
                           | Q(invoice_number__icontains=f["q"]) | Q(supplier__name__icontains=f["q"]))
        if f["category"]:
            qs = qs.filter(category=f["category"])
        if f["supplier"]:
            qs = qs.filter(supplier_id=f["supplier"])
        if f["status"] != "all":
            qs = qs.filter(status=f["status"])
        if f["date_from"]:
            qs = qs.filter(expense_date__gte=f["date_from"])
        if f["date_to"]:
            qs = qs.filter(expense_date__lte=f["date_to"])
        self.total = qs.filter(status=Expense.Status.ACTIVE).aggregate(
            t=Coalesce(Sum("amount"), 0, output_field=MONEY))["t"]
        return qs

    def get_context_data(self, **kwargs):
        f = self.filters
        return super().get_context_data(
            filters={**f, "date_from": f["date_from"] or "", "date_to": f["date_to"] or ""},
            categories=ExpenseCategory.choices, suppliers=Supplier.objects.all(), total=self.total, **kwargs)


class ExpenseCreateView(ModulePermissionMixin, PageMetaMixin, CreateView):
    permission_required = "accounts.manage_expenses"
    form_class = ExpenseForm
    template_name = "expenses/form.html"
    page_title = "Record Expense"

    def form_valid(self, form):
        form.instance.recorded_by = self.request.user
        response = super().form_valid(form)
        e = self.object
        log_activity(ActivityLog.Action.EXPENSE_RECORDED,
                     f"{e.reference}: {e.get_category_display()} LKR {e.amount:,.2f}", request=self.request)
        messages.success(self.request, f"Expense {e.reference} recorded.")
        return response


class ExpenseUpdateView(ModulePermissionMixin, PageMetaMixin, UpdateView):
    permission_required = "accounts.manage_expenses"
    model = Expense
    form_class = ExpenseForm
    template_name = "expenses/form.html"
    page_title = "Edit Expense"

    def get_object(self, queryset=None):
        expense = super().get_object(queryset)
        if expense.is_linked or expense.status == Expense.Status.VOIDED:
            raise PermissionDenied  # linked expenses follow their source; voided ones are final
        return expense

    def form_valid(self, form):
        messages.success(self.request, "Expense updated.")
        log_activity(ActivityLog.Action.EXPENSE_RECORDED, f"{self.object.reference} edited", request=self.request)
        return super().form_valid(form)


class ExpenseDetailView(ModulePermissionMixin, PageMetaMixin, DetailView):
    permission_required = "accounts.view_expenses"
    model = Expense
    template_name = "expenses/detail.html"
    context_object_name = "expense"

    def get_page_title(self):
        return f"Expense {self.object.reference}"

    def get_context_data(self, **kwargs):
        e = self.object
        user = self.request.user
        source = None
        if e.source == Expense.Source.MAINTENANCE and hasattr(e, "maintenance_record"):
            source = e.maintenance_record.equipment
        elif e.source == Expense.Source.EQUIPMENT and hasattr(e, "purchased_equipment"):
            source = e.purchased_equipment
        elif e.source == Expense.Source.STOCK and hasattr(e, "stock_movement"):
            source = e.stock_movement.item
        return super().get_context_data(
            breadcrumbs=[("Expenses", reverse("expenses:list"))], source=source,
            can_manage=user.has_perm("accounts.manage_expenses") and e.status == Expense.Status.ACTIVE,
            reason_form=ReasonForm(), **kwargs)


class ExpenseVoidView(ModulePermissionMixin, View):
    permission_required = "accounts.manage_expenses"
    http_method_names = ["post"]

    def post(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        form = ReasonForm(request.POST)
        try:
            services.void_expense(expense, request.user, form.cleaned_data["reason"] if form.is_valid() else "", request)
        except services.ExpenseError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Expense voided. It stays in the records but no longer counts in totals.")
        return redirect(expense.get_absolute_url())


class ExpenseReceiptView(ModulePermissionMixin, View):
    permission_required = "accounts.view_expenses"

    def get(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        if not expense.receipt:
            raise Http404
        response = FileResponse(expense.receipt.open("rb"),
                                content_type=mimetypes.guess_type(expense.receipt.name)[0] or "application/octet-stream")
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class SupplierAccessMixin:
    def dispatch(self, request, *args, **kwargs):
        if not any(request.user.has_perm(p) for p in SUPPLIER_PERMS):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class SupplierListView(SupplierAccessMixin, PageMetaMixin, ListView):
    model = Supplier
    template_name = "expenses/suppliers.html"
    context_object_name = "suppliers"
    paginate_by = 50
    page_title = "Suppliers"


class SupplierCreateView(SupplierAccessMixin, PageMetaMixin, CreateView):
    form_class = SupplierForm
    template_name = "expenses/supplier_form.html"
    page_title = "New Supplier"

    def get_success_url(self):
        messages.success(self.request, "Supplier saved.")
        return reverse("expenses:suppliers")


class SupplierUpdateView(SupplierCreateView, UpdateView):
    model = Supplier
    page_title = "Edit Supplier"
