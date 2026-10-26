from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, FormView, ListView, TemplateView

from apps.core.mixins import ModulePermissionMixin, PageMetaMixin
from apps.memberships.forms import ReasonForm
from apps.memberships.models import Membership
from apps.trainers.access import can_view_sessions

from . import services
from .balances import (
    ZERO,
    balance_for,
    balance_from_annotations,
    is_appointment,
    refundable_amount,
    with_appointment_balances,
    with_balances,
)
from .forms import AdjustmentForm, PaymentForm, RefundForm
from .models import Payment, PaymentMethod
from .receipts import receipt_context, render_receipt_pdf

MONEY = DecimalField(max_digits=12, decimal_places=2)


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def payments_crumb():
    return [("Payments", reverse("payments:list"))]


def membership_crumbs(membership):
    return [("Members", reverse("members:list")),
            (membership.member.full_name, membership.member.get_absolute_url()),
            (membership.plan_name, membership.get_absolute_url())]


def item_summary(target):
    """Display data for the balance summary card (membership or session)."""
    if is_appointment(target):
        start = timezone.localtime(target.starts_at)
        return {"label": f"{target.session_type.name} with {target.trainer.name}", "member": target.member,
                "detail": f"{start:%d %b %Y, %H:%M}", "price_label": "Session fee", "price": target.fee,
                "url": target.get_absolute_url()}
    return {"label": target.plan_name, "member": target.member, "price_label": "Agreed price", "price": target.price,
            "detail": f"{target.start_date:%d %b %Y} – {target.end_date:%d %b %Y}", "url": target.get_absolute_url()}


class PaymentListView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.view_payments"
    template_name = "payments/list.html"
    context_object_name = "payments"
    paginate_by = 25
    page_title = "Payments"

    def get_queryset(self):
        g = self.request.GET
        self.filters = f = {
            "q": g.get("q", "").strip()[:100],
            "kind": g.get("kind", "") if g.get("kind") in Payment.Kind.values else "",
            "status": g.get("status", "") if g.get("status") in Payment.Status.values else "",
            "method": g.get("method", "") if g.get("method") in PaymentMethod.values else "",
            "date_from": _parse_date(g.get("date_from")),
            "date_to": _parse_date(g.get("date_to")),
        }
        qs = Payment.objects.select_related("member", "membership", "appointment__session_type",
                                            "appointment__trainer__user", "recorded_by")
        if f["q"]:
            q = f["q"]
            qs = qs.filter(
                Q(reference__iexact=q) | Q(receipt_number__iexact=q) | Q(external_reference__icontains=q)
                | Q(member__full_name__icontains=q) | Q(member__member_id__iexact=q)
            )
        for key in ("kind", "status", "method"):
            if f[key]:
                qs = qs.filter(**{key: f[key]})
        if f["date_from"]:
            qs = qs.filter(paid_on__gte=f["date_from"])
        if f["date_to"]:
            qs = qs.filter(paid_on__lte=f["date_to"])
        self.filtered = qs
        return qs.order_by("-paid_on", "-pk")

    def get_context_data(self, **kwargs):
        completed = self.filtered.filter(status=Payment.Status.COMPLETED)
        totals = completed.aggregate(
            received=Coalesce(Sum("amount", filter=Q(kind=Payment.Kind.PAYMENT)), ZERO, output_field=MONEY),
            refunded=Coalesce(Sum("amount", filter=Q(kind=Payment.Kind.REFUND)), ZERO, output_field=MONEY),
        )
        totals["net"] = totals["received"] - totals["refunded"]
        totals["pending"] = self.filtered.filter(status=Payment.Status.PENDING).aggregate(
            t=Coalesce(Sum("amount"), ZERO, output_field=MONEY))["t"]
        f = self.filters
        return super().get_context_data(
            filters={**f, "date_from": f["date_from"] or "", "date_to": f["date_to"] or ""},
            is_filtered=any(f.values()), totals=totals,
            kinds=Payment.Kind.choices, statuses=Payment.Status.choices, methods=PaymentMethod.choices,
            **kwargs,
        )


class OutstandingView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.view_payments"
    template_name = "payments/outstanding.html"
    context_object_name = "memberships"
    paginate_by = 25
    page_title = "Outstanding Balances"

    def get_queryset(self):
        self.q = self.request.GET.get("q", "").strip()[:100]
        qs = with_balances(Membership.objects.select_related("member")).filter(outstanding__gt=0)
        if self.q:
            qs = qs.filter(Q(member__full_name__icontains=self.q) | Q(member__member_id__iexact=self.q))
        self.total = qs.aggregate(t=Coalesce(Sum("outstanding"), ZERO, output_field=MONEY))["t"]
        return qs.order_by("-outstanding", "start_date")

    def get_context_data(self, **kwargs):
        from apps.appointments.models import Appointment

        sessions = with_appointment_balances(
            Appointment.objects.select_related("member", "trainer__user", "session_type")
        ).filter(outstanding__gt=0)
        if self.q:
            sessions = sessions.filter(Q(member__full_name__icontains=self.q) | Q(member__member_id__iexact=self.q))
        sessions = list(sessions.order_by("starts_at")[:50])
        session_total = sum((s.outstanding for s in sessions), ZERO)
        context = super().get_context_data(filters={"q": self.q}, total=self.total + session_total,
                                           membership_total=self.total, sessions=sessions,
                                           session_total=session_total, breadcrumbs=payments_crumb(), **kwargs)
        for m in context["memberships"]:
            m.balance = balance_from_annotations(m)
        return context


class PaymentDetailView(ModulePermissionMixin, PageMetaMixin, DetailView):
    permission_required = "accounts.view_payments"
    model = Payment
    template_name = "payments/detail.html"
    context_object_name = "payment"

    def get_queryset(self):
        return Payment.objects.select_related("member", "membership", "appointment", "refund_of", "recorded_by",
                                              "confirmed_by", "voided_by")

    def get_page_title(self):
        return f"{self.object.get_kind_display()} {self.object.reference}"

    def get_context_data(self, **kwargs):
        p = self.object
        user = self.request.user
        can_refund = user.has_perm("accounts.refund_payments")
        has_live_refunds = p.refunds.exclude(status=Payment.Status.VOIDED).exists()
        return super().get_context_data(
            breadcrumbs=payments_crumb(),
            balance=balance_for(p.target),
            refunds=p.refunds.select_related("recorded_by"),
            refundable=refundable_amount(p),
            can_confirm=user.has_perm("accounts.manage_payments") and p.status == Payment.Status.PENDING,
            can_refund=can_refund and refundable_amount(p) > 0,
            can_void=can_refund and p.status != Payment.Status.VOIDED and not has_live_refunds,
            reason_form=ReasonForm(),
            **kwargs,
        )


class MembershipFormMixin:
    def dispatch(self, request, *args, **kwargs):
        self.membership = get_object_or_404(Membership.objects.select_related("member"),
                                            pk=kwargs["membership_pk"])
        return super().dispatch(request, *args, **kwargs)


class RecordPaymentView(ModulePermissionMixin, PageMetaMixin, FormView):
    """Record a payment for a membership, or (``appointment_pk``) a training session fee."""

    permission_required = "accounts.manage_payments"
    form_class = PaymentForm
    template_name = "payments/record.html"

    def dispatch(self, request, *args, **kwargs):
        from apps.appointments.models import Appointment

        if "appointment_pk" in kwargs:
            self.target = get_object_or_404(Appointment.objects.select_related("member", "trainer__user",
                                                                               "session_type"), pk=kwargs["appointment_pk"])
        else:
            self.target = get_object_or_404(Membership.objects.select_related("member"), pk=kwargs["membership_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_page_title(self):
        return f"Record Payment · {self.target.member.full_name}"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "payable": balance_for(self.target).payable}

    def get_context_data(self, **kwargs):
        if not is_appointment(self.target):
            crumbs = membership_crumbs(self.target)
        elif can_view_sessions(self.request.user):
            crumbs = [("Appointments", reverse("appointments:list")), ("Session", self.target.get_absolute_url())]
        else:
            crumbs = [("Outstanding", reverse("payments:outstanding"))]
        item = item_summary(self.target)
        if crumbs[-1][0] == "Outstanding":
            item["url"] = reverse("payments:outstanding")  # "Cancel" must not lead to a page this user can't open
        return super().get_context_data(item=item, balance=balance_for(self.target), breadcrumbs=crumbs, **kwargs)

    def form_valid(self, form):
        d = form.cleaned_data
        try:
            payment, created = services.record_payment(
                self.target, d["amount"], d["method"], d["paid_on"], self.request.user,
                reference=d["external_reference"], notes=d["notes"], token=d["submission_token"],
                pending=d["pending"], request=self.request,
            )
        except services.PaymentError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        if not created:
            messages.info(self.request, f"This payment was already recorded as {payment.reference}.")
        elif payment.status == Payment.Status.PENDING:
            messages.success(self.request, f"Pending payment {payment.reference} recorded. "
                                           "Confirm it once the transfer has cleared.")
        else:
            messages.success(self.request, f"Payment {payment.reference} recorded. Receipt {payment.receipt_number}.")
        return redirect(payment.get_absolute_url())


class RefundView(ModulePermissionMixin, PageMetaMixin, FormView):
    permission_required = "accounts.refund_payments"
    form_class = RefundForm
    template_name = "payments/refund.html"

    def dispatch(self, request, *args, **kwargs):
        self.payment = get_object_or_404(Payment.objects.select_related("member", "membership"), pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_page_title(self):
        return f"Refund {self.payment.reference}"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "refundable": refundable_amount(self.payment),
                "method": self.payment.method}

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            payment=self.payment, refundable=refundable_amount(self.payment),
            breadcrumbs=payments_crumb() + [(self.payment.reference, self.payment.get_absolute_url())], **kwargs,
        )

    def form_valid(self, form):
        d = form.cleaned_data
        try:
            refund, created = services.refund_payment(
                self.payment, d["amount"], d["method"], d["paid_on"], self.request.user, d["reason"],
                token=d["submission_token"], request=self.request,
            )
        except services.PaymentError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        if created:
            messages.success(self.request, f"Refund {refund.reference} recorded. Receipt {refund.receipt_number}.")
        else:
            messages.info(self.request, f"This refund was already recorded as {refund.reference}.")
        return redirect(refund.get_absolute_url())


class AdjustBalanceView(ModulePermissionMixin, PageMetaMixin, MembershipFormMixin, FormView):
    permission_required = "accounts.refund_payments"
    form_class = AdjustmentForm
    template_name = "payments/adjust.html"

    def get_page_title(self):
        return f"Adjust Balance · {self.membership.member.full_name}"

    def get_context_data(self, **kwargs):
        return super().get_context_data(membership=self.membership, balance=balance_for(self.membership),
                                        item=item_summary(self.membership),
                                        breadcrumbs=membership_crumbs(self.membership), **kwargs)

    def form_valid(self, form):
        d = form.cleaned_data
        try:
            services.adjust_balance(self.membership, d["kind"], d["amount"], d["reason"], self.request.user,
                                    self.request)
        except services.PaymentError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, "Balance adjusted.")
        return redirect(self.membership.get_absolute_url())


class PaymentActionView(ModulePermissionMixin, View):
    http_method_names = ["post"]
    needs_reason = False

    def post(self, request, pk):
        payment = get_object_or_404(Payment, pk=pk)
        reason = ""
        if self.needs_reason:
            form = ReasonForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Please give a reason (at least 3 characters).")
                return redirect(payment.get_absolute_url())
            reason = form.cleaned_data["reason"]
        try:
            message = self.perform(payment, reason)
        except services.PaymentError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, message)
        return redirect(payment.get_absolute_url())


class ConfirmPaymentView(PaymentActionView):
    permission_required = "accounts.manage_payments"

    def perform(self, payment, reason):
        p = services.confirm_payment(payment, self.request.user, self.request)
        return f"Payment confirmed. Receipt {p.receipt_number} issued."


class VoidPaymentView(PaymentActionView):
    permission_required = "accounts.refund_payments"
    needs_reason = True

    def perform(self, payment, reason):
        services.void_payment(payment, self.request.user, reason, self.request)
        return "Transaction voided. It stays in the history but no longer counts towards any balance."


class ReceiptAccessMixin:
    """Receipts contain member details: staff with view_payments, or the member themself."""

    def get_payment(self):
        payment = get_object_or_404(
            Payment.objects.select_related("member", "membership", "appointment", "recorded_by", "refund_of"),
            pk=self.kwargs["pk"]
        )
        user = self.request.user
        is_own = payment.member.user_id is not None and payment.member.user_id == user.pk
        if not (user.has_perm("accounts.view_payments") or is_own):
            raise PermissionDenied
        if not payment.receipt_number:
            raise Http404("No receipt has been issued for this transaction.")
        return payment


class ReceiptView(ReceiptAccessMixin, TemplateView):
    template_name = "payments/receipt.html"

    def get_context_data(self, **kwargs):
        payment = self.get_payment()
        return super().get_context_data(**receipt_context(payment), page_title=f"Receipt {payment.receipt_number}",
                                        **kwargs)


class ReceiptPDFView(ReceiptAccessMixin, View):
    def get(self, request, pk):
        payment = self.get_payment()
        pdf = render_receipt_pdf(receipt_context(payment))
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{payment.receipt_number}.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response
