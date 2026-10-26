from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.core.utils import paginate

from . import services
from .forms import ItemForm, MovementForm
from .models import InventoryItem, StockMovement

USE_KINDS = {StockMovement.Kind.USAGE, StockMovement.Kind.SALE}


def allowed_kinds(user):
    if user.has_perm("accounts.manage_inventory"):
        return set(StockMovement.Kind.values)
    if user.has_perm("accounts.use_inventory"):
        return USE_KINDS
    return set()


class InventoryViewerMixin:
    def dispatch(self, request, *args, **kwargs):
        if not allowed_kinds(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class ItemListView(InventoryViewerMixin, PageMetaMixin, ListView):
    template_name = "inventory/list.html"
    context_object_name = "items"
    paginate_by = 30
    page_title = "Inventory"

    def get_queryset(self):
        g = self.request.GET
        self.filters = f = {
            "q": g.get("q", "").strip()[:100],
            "category": g.get("category", "") if g.get("category") in InventoryItem.Category.values else "",
            "low": g.get("low") == "1",
            "inactive": g.get("inactive") == "1",
        }
        qs = InventoryItem.objects.select_related("supplier")
        if not f["inactive"]:
            qs = qs.filter(is_active=True)
        if f["q"]:
            qs = qs.filter(Q(name__icontains=f["q"]) | Q(sku__iexact=f["q"]))
        if f["category"]:
            qs = qs.filter(category=f["category"])
        if f["low"]:
            qs = qs.filter(quantity__lte=F("minimum_stock"))
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(filters=self.filters, categories=InventoryItem.Category.choices,
                                        low_count=services.low_stock_items().count(), **kwargs)


class ItemDetailView(InventoryViewerMixin, PageMetaMixin, DetailView):
    model = InventoryItem
    template_name = "inventory/detail.html"
    context_object_name = "item"

    def get_page_title(self):
        return self.object.name

    def get_context_data(self, **kwargs):
        kinds = allowed_kinds(self.request.user)
        return super().get_context_data(
            breadcrumbs=[("Inventory", reverse("inventory:list"))],
            movements=paginate(self.request, self.object.movements.select_related("recorded_by", "supplier", "expense"), 25),
            movement_form=MovementForm(allowed_kinds=kinds, initial={"kind": next(
                (k for k in ("purchase", "usage", "sale", "adjustment") if k in kinds), None)}),
            can_manage=self.request.user.has_perm("accounts.manage_inventory"),
            can_see_expenses=self.request.user.has_perm("accounts.view_expenses"), **kwargs)


class ItemCreateView(ModulePermissionMixin, PageMetaMixin, CreateView):
    permission_required = "accounts.manage_inventory"
    form_class = ItemForm
    template_name = "inventory/form.html"
    page_title = "New Inventory Item"

    def form_valid(self, form):
        messages.success(self.request, "Item created. Record a purchase to add stock.")
        return super().form_valid(form)


class ItemUpdateView(ModulePermissionMixin, PageMetaMixin, UpdateView):
    permission_required = "accounts.manage_inventory"
    model = InventoryItem
    form_class = ItemForm
    template_name = "inventory/form.html"
    page_title = "Edit Inventory Item"

    def form_valid(self, form):
        response = super().form_valid(form)
        services._check_low_stock(self.object)  # minimum may have changed
        messages.success(self.request, "Item saved.")
        return response


class RecordMovementView(InventoryViewerMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        item = get_object_or_404(InventoryItem, pk=pk)
        kinds = allowed_kinds(request.user)
        form = MovementForm(request.POST, allowed_kinds=kinds)
        if not form.is_valid():
            messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
            return redirect(item.get_absolute_url())
        d = form.cleaned_data
        if d["kind"] not in kinds:
            raise PermissionDenied
        try:
            movement = services.record_movement(
                item, d["kind"], d["quantity"], request.user, unit_cost=d["unit_cost"] if d["kind"] == "purchase" else None,
                supplier=d["supplier"], reference=d["reference"], reason=d["reason"], request=request)
        except services.StockError as exc:
            messages.error(request, str(exc))
        else:
            note = f" Cost recorded as expense {movement.expense.reference}." if movement.expense_id else ""
            messages.success(request, f"Stock updated: {movement.quantity_after.normalize():f} {item.unit} now.{note}")
        return redirect(item.get_absolute_url())
