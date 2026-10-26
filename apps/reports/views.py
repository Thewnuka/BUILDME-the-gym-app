from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from apps.core import gym
from apps.core.mixins import ModulePermissionMixin, PageMetaMixin

from .base import fmt_value, to_csv, to_pdf
from .definitions import REGISTRY


class ReportIndexView(ModulePermissionMixin, PageMetaMixin, TemplateView):
    permission_required = "accounts.view_reports"
    template_name = "reports/index.html"
    page_title = "Reports"

    def get_context_data(self, **kwargs):
        sections = {}
        for cls in REGISTRY.values():
            if cls.allowed(self.request.user):
                sections.setdefault(cls.section, []).append(cls)
        return super().get_context_data(sections=sections, **kwargs)


class ReportView(View):
    """One report: HTML page, ?export=csv, ?export=pdf or ?print=1."""

    def dispatch(self, request, slug):
        cls = REGISTRY.get(slug)
        if cls is None:
            raise Http404
        if not cls.allowed(request.user):
            raise PermissionDenied
        self.report = cls(request.user)
        return super().dispatch(request, slug)

    def get(self, request, slug):
        report = self.report
        params = report.params(request.GET)
        rows = report.rows(params)
        summary = report.summary(params, rows)
        export = request.GET.get("export")
        stamp = f"{params.date_from:%Y%m%d}-{params.date_to:%Y%m%d}" if report.uses_dates else f"{timezone.localdate():%Y%m%d}"
        filename = f"{report.slug}-{stamp}"
        if export == "csv":
            response = HttpResponse(to_csv(report, params, rows), content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
            return response
        if export == "pdf":
            response = HttpResponse(to_pdf(report, params, rows, summary, gym.gym_name()),
                                    content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
            return response
        printing = request.GET.get("print") == "1"
        page = None if printing else Paginator(rows, 50).get_page(request.GET.get("page"))
        visible = rows if printing else page.object_list
        table = [{"url": r.get("_url"), "cells": [(fmt_value(r.get(c.key), c.fmt), c.align) for c in report.columns]}
                 for r in visible]
        from django.shortcuts import render

        return render(request, "reports/print.html" if printing else "reports/report.html", {
            "report": report, "params": params, "summary": [(label, fmt_value(v, f)) for label, v, f in summary],
            "table": table, "page_obj": page, "is_paginated": bool(page and page.paginator.num_pages > 1),
            "row_count": len(rows), "chart": report.chart(params, rows) if not printing else None,
            "filters": report.filter_controls(params), "page_title": report.title, "generated": timezone.localtime(),
            "breadcrumbs": [("Reports", "/reports/")],
        })
