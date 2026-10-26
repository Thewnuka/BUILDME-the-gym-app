"""
Report framework. Each report declares its columns, permissions and how to
build rows from the database; the generic view adds date filters, search,
pagination, CSV export, a printable page and PDF export.
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

FORMATS = ("text", "int", "money", "date", "decimal", "pct")


@dataclass
class Column:
    key: str
    label: str
    fmt: str = "text"
    align: str = ""

    def __post_init__(self):
        if not self.align:
            self.align = "end" if self.fmt in ("int", "money", "decimal", "pct") else "start"


@dataclass
class Params:
    date_from: date
    date_to: date
    group: str = "day"
    q: str = ""
    extra: dict = field(default_factory=dict)


def parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


class Report:
    do_not_call_in_templates = True
    slug = ""
    title = ""
    description = ""
    section = "General"
    permissions = ()           # all required, in addition to view_reports
    any_permissions = ()       # at least one required (if set)
    uses_dates = True
    default_days = 30
    searchable = False
    groupable = False          # day / month grouping
    columns = []
    max_range_days = 731

    def __init__(self, user):
        self.user = user

    @classmethod
    def allowed(cls, user):
        if not user.has_perm("accounts.view_reports"):
            return False
        if any(not user.has_perm(p) for p in cls.permissions):
            return False
        return not cls.any_permissions or any(user.has_perm(p) for p in cls.any_permissions)

    def params(self, data):
        today = timezone.localdate()
        date_to = parse_date(data.get("date_to")) or today
        date_from = parse_date(data.get("date_from")) or date_to - timedelta(days=self.default_days - 1)
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        if (date_to - date_from).days > self.max_range_days:
            date_from = date_to - timedelta(days=self.max_range_days)
        group = data.get("group", "day") if data.get("group") in ("day", "month") else "day"
        return Params(date_from, date_to, group, (data.get("q") or "").strip()[:100], self.extra_params(data))

    def extra_params(self, data):
        return {}

    def filter_controls(self, params):
        """Extra <select> filters: [(name, label, [(value, label)], current)]."""
        return []

    def rows(self, params):
        raise NotImplementedError

    def summary(self, params, rows):
        """Headline figures: [(label, value, fmt)]."""
        return []

    def chart(self, params, rows):
        return None

    def notes(self):
        return ""


def fmt_value(value, fmt):
    if value is None or value == "":
        return "—"
    if fmt == "money":
        return f"LKR {Decimal(value):,.2f}"
    if fmt == "int":
        return f"{int(value):,}"
    if fmt == "decimal":
        return f"{Decimal(value).normalize():f}"
    if fmt == "pct":
        return f"{value:.1f}%"
    if fmt == "date":
        return value.strftime("%d %b %Y") if hasattr(value, "strftime") else str(value)
    return str(value)


def _csv_safe(value):
    """Neutralise spreadsheet formula injection (=, +, -, @ at the start of text)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def to_csv(report, params, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([c.label for c in report.columns])
    for row in rows:
        out = []
        for c in report.columns:
            v = row.get(c.key)
            if c.fmt == "date" and hasattr(v, "isoformat"):
                v = v.isoformat()
            elif c.fmt in ("money", "decimal") and v is not None:
                v = f"{Decimal(v):.2f}"
            out.append(_csv_safe("" if v is None else v))
        writer.writerow(out)
    return "﻿" + buffer.getvalue()  # BOM so Excel opens UTF-8 correctly


def to_pdf(report, params, rows, summary, gym_name):
    from fpdf import FPDF

    from apps.payments.receipts import _latin

    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 8, _latin(f"{gym_name} - {report.title}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    period = (f"{params.date_from:%d %b %Y} to {params.date_to:%d %b %Y}" if report.uses_dates
              else f"As at {timezone.localdate():%d %b %Y}")
    pdf.cell(0, 6, _latin(f"{period}   ·   generated {timezone.localtime():%d %b %Y %H:%M}"), new_x="LMARGIN", new_y="NEXT")
    if summary:
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 9)
        line = "   |   ".join(f"{label}: {fmt_value(value, fmt)}" for label, value, fmt in summary)
        pdf.multi_cell(0, 5, _latin(line), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    width = (pdf.w - pdf.l_margin - pdf.r_margin) / max(len(report.columns), 1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(240, 240, 240)
    for c in report.columns:
        pdf.cell(width, 7, _latin(c.label)[:40], border=1, fill=True, align="R" if c.align == "end" else "L")
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for row in rows[:3000]:
        for c in report.columns:
            text = _latin(fmt_value(row.get(c.key), c.fmt))
            while text and pdf.get_string_width(text) > width - 2:
                text = text[:-2] + "…" if len(text) > 2 else text[:-1]
            pdf.cell(width, 6, text.replace("…", "~"), border=1, align="R" if c.align == "end" else "L")
        pdf.ln()
    if len(rows) > 3000:
        pdf.cell(0, 6, f"First 3000 of {len(rows)} rows. Use CSV export for the full data.")
    return bytes(pdf.output())
