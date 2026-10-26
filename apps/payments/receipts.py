"""Receipt data and PDF rendering."""
import unicodedata
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone
from fpdf import FPDF

from apps.core import gym

from .models import BalanceAdjustment, Payment

ZERO = Decimal("0.00")


def receipt_context(payment):
    """
    Figures as they stood when this transaction was recorded: later payments,
    refunds or adjustments never change an earlier receipt.
    """
    membership = payment.membership
    as_of = payment.confirmed_at or payment.recorded_at
    if membership:
        adjustments = BalanceAdjustment.objects.filter(membership=membership, created_at__lte=as_of)
        adj_total = sum((a.signed_amount for a in adjustments), ZERO)
        charge = membership.price + adj_total
        ledger = Payment.objects.filter(membership=membership)
    else:
        adj_total, charge = ZERO, payment.appointment.fee
        ledger = Payment.objects.filter(appointment=payment.appointment)
    ledger = ledger.filter(status=Payment.Status.COMPLETED).filter(Q(pk__lte=payment.pk))
    paid = ledger.filter(kind=Payment.Kind.PAYMENT).aggregate(t=Sum("amount"))["t"] or ZERO
    refunded = ledger.filter(kind=Payment.Kind.REFUND).aggregate(t=Sum("amount"))["t"] or ZERO
    net_paid = paid - refunded
    return {
        "payment": payment,
        "member": payment.member,
        "membership": membership,
        "gym_name": gym.gym_name(),
        "gym": gym.contact(),
        "amount_due": charge,
        "adjustments_total": adj_total,
        "total_paid": net_paid,
        "outstanding": max(charge - net_paid, ZERO),
        "is_refund": payment.is_refund,
        "is_void": payment.status == Payment.Status.VOIDED,
        "generated_at": timezone.localtime(),
    }


def _latin(text):
    """Built-in PDF fonts are Latin-1 only; transliterate anything else."""
    text = str(text)
    normalized = unicodedata.normalize("NFKD", text)
    out = normalized.encode("latin-1", "ignore").decode("latin-1")
    return out if out.strip() else text.encode("ascii", "replace").decode("ascii")


def _money(value):
    return f"LKR {value:,.2f}"


class _ReceiptPDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(130)
        self.cell(0, 8, _latin(f"{gym.gym_name()} - computer-generated receipt"), align="C")


def render_receipt_pdf(ctx):
    p, m = ctx["payment"], ctx["member"]
    pdf = _ReceiptPDF(format=(148, 210))  # A5 in mm
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_title(f"{p.receipt_number} - {gym.gym_name()}")

    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(242, 84, 27)
    pdf.cell(0, 9, _latin(ctx["gym_name"]), new_x="LMARGIN", new_y="NEXT")
    gym_info = ctx.get("gym") or {}
    contact = [", ".join(line.strip() for line in gym_info.get("address", "").splitlines() if line.strip()),
               " - ".join(v for v in (gym_info.get("phone"), gym_info.get("email")) if v)]
    if gym_info.get("registration"):
        contact.append(f"Reg. {gym_info['registration']}")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(110)
    for line in filter(None, contact):
        pdf.multi_cell(0, 4, _latin(line), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(30)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "REFUND RECEIPT" if ctx["is_refund"] else "PAYMENT RECEIPT", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    def row(label, value, bold=False):
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(110)
        pdf.cell(42, 6, _latin(label))
        pdf.set_text_color(30)
        pdf.set_font("Helvetica", "B" if bold else "", 10)
        pdf.multi_cell(0, 6, _latin(value), new_x="LMARGIN", new_y="NEXT")

    row("Receipt number", p.receipt_number or "-", bold=True)
    row("Transaction ref.", p.reference)
    row("Payment date", p.paid_on.strftime("%d %b %Y"))
    row("Payment method", p.get_method_display())
    if p.external_reference:
        row("Bank / card ref.", p.external_reference)
    if p.is_refund and p.refund_of_id:
        row("Refund of", p.refund_of.reference)
    pdf.ln(2)
    row("Member", f"{m.full_name} ({m.member_id})")
    row("For", p.item_label)
    row("Details", p.item_detail.replace("–", "to"))
    pdf.ln(3)
    pdf.set_draw_color(220)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3)
    row("Amount due", _money(ctx["amount_due"]))
    row("Refunded" if ctx["is_refund"] else "Amount paid", _money(p.amount), bold=True)
    row("Total paid to date", _money(ctx["total_paid"]))
    row("Outstanding balance", _money(ctx["outstanding"]), bold=True)
    pdf.ln(3)
    row("Recorded by", p.recorded_by.get_full_name() or p.recorded_by.username)
    row("Issued", ctx["generated_at"].strftime("%d %b %Y %H:%M"))
    if (ctx.get("gym") or {}).get("receipt_footer"):
        pdf.ln(4)
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(80)
        pdf.multi_cell(0, 5, _latin(ctx["gym"]["receipt_footer"]), align="C", new_x="LMARGIN", new_y="NEXT")

    if ctx["is_void"]:
        pdf.set_font("Helvetica", "B", 56)
        pdf.set_text_color(220, 38, 38)
        with pdf.rotation(30, x=pdf.w / 2, y=pdf.h / 2):
            pdf.text(pdf.w / 2 - 30, pdf.h / 2, "VOID")
    return bytes(pdf.output())
