from django.contrib.auth.mixins import PermissionRequiredMixin, UserPassesTestMixin


class ModulePermissionMixin(PermissionRequiredMixin):
    """Server-side permission check. Logged-in users without access get HTTP 403."""

    raise_exception = True


class SuperAdminRequiredMixin(UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        return self.request.user.is_super_admin


class PageMetaMixin:
    """Provides the page title and breadcrumbs to templates."""

    page_title = ""
    breadcrumbs = ()

    def get_page_title(self):
        return self.page_title

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("page_title", self.get_page_title())
        context.setdefault("breadcrumbs", self.breadcrumbs)
        return context
