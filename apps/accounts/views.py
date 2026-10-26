from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView

from apps.core import gym
from apps.core.mixins import ModulePermissionMixin, PageMetaMixin, SuperAdminRequiredMixin
from apps.core.models import ActivityLog
from apps.core.utils import log_activity

from .forms import (
    AdminSetPasswordForm,
    LoginForm,
    ProfileForm,
    RegistrationForm,
    StyledPasswordChangeForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
    UserCreateForm,
    UserUpdateForm,
)
from .models import User
from .roles import MODULE_PERMISSIONS, RESERVED_PERMISSIONS, Role, can_manage_user
from .services import role_permission_codenames, send_account_setup_email, set_role_permissions

Action = ActivityLog.Action


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
class LoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        if not form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(0)  # expire when the browser closes
        return super().form_valid(form)


class LogoutView(auth_views.LogoutView):
    pass


@method_decorator(login_not_required, name="dispatch")
@method_decorator(sensitive_post_parameters("password1", "password2"), name="dispatch")
class RegisterView(CreateView):
    """Public sign-up. Always creates an active Member account."""

    form_class = RegistrationForm
    template_name = "registration/register.html"
    success_url = reverse_lazy("accounts:login")

    def dispatch(self, request, *args, **kwargs):
        if not settings.REGISTRATION_ENABLED:
            raise Http404
        if request.user.is_authenticated:
            return redirect(settings.LOGIN_REDIRECT_URL)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.object
        log_activity(Action.USER_REGISTERED, f"{user.username} registered a new account",
                     actor=user, request=self.request)
        messages.success(self.request,
                         f"Welcome to {gym.gym_name()}, {user.first_name}! Your account is ready. Please sign in.")
        return response


class PasswordChangeView(PageMetaMixin, auth_views.PasswordChangeView):
    template_name = "registration/password_change_form.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("accounts:profile")
    page_title = "Change Password"

    def form_valid(self, form):
        response = super().form_valid(form)
        log_activity(Action.PASSWORD_CHANGED, f"{self.request.user.username} changed their password", request=self.request)
        messages.success(self.request, "Your password has been changed.")
        return response


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.txt"
    subject_template_name = "registration/password_reset_subject.txt"
    form_class = StyledPasswordResetForm
    success_url = reverse_lazy("accounts:password_reset_done")
    extra_email_context = {"brand": gym.gym_name()}


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "registration/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "registration/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        log_activity(Action.PASSWORD_RESET, f"{form.user.username} set a new password via email link",
                     actor=form.user, request=self.request)
        return response


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "registration/password_reset_complete.html"


# --------------------------------------------------------------------------
# Own profile (available to every role, including members)
# --------------------------------------------------------------------------
class ProfileView(PageMetaMixin, TemplateView):
    template_name = "accounts/profile.html"
    page_title = "My Profile"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["profile_user"] = self.request.user
        context["activities"] = ActivityLog.objects.filter(actor=self.request.user)[:10]
        return context


class ProfileUpdateView(PageMetaMixin, UpdateView):
    template_name = "accounts/profile_form.html"
    form_class = ProfileForm
    page_title = "Edit Profile"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        response = super().form_valid(form)
        log_activity(Action.PROFILE_UPDATED, f"{self.object.username} updated their profile", request=self.request)
        messages.success(self.request, "Your profile has been updated.")
        return response


# --------------------------------------------------------------------------
# User management
# --------------------------------------------------------------------------
class ManagedUserMixin:
    """Loads the target user and enforces the role hierarchy (HTTP 403)."""

    def get_target(self):
        if not hasattr(self, "_target"):
            self._target = get_object_or_404(User, pk=self.kwargs["pk"])
            if not can_manage_user(self.request.user, self._target):
                raise PermissionDenied
        return self._target


class UserListView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.view_users"
    template_name = "accounts/users/list.html"
    context_object_name = "users"
    paginate_by = 15
    page_title = "Users"

    def get_queryset(self):
        qs = User.objects.all().order_by("-date_joined")
        q = self.request.GET.get("q", "").strip()
        role = self.request.GET.get("role", "")
        status = self.request.GET.get("status", "")
        if q:
            qs = qs.filter(
                Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                | Q(email__icontains=q) | Q(phone__icontains=q)
            )
        if role in Role.values:
            qs = qs.filter(role=role)
        if status == "active":
            qs = qs.filter(is_active=True)
        elif status == "inactive":
            qs = qs.filter(is_active=False)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["roles"] = Role.choices
        context["filters"] = {k: self.request.GET.get(k, "") for k in ("q", "role", "status")}
        context["is_filtered"] = any(context["filters"].values())
        context["can_manage"] = self.request.user.has_perm("accounts.manage_users")
        return context


class UserDetailView(ModulePermissionMixin, PageMetaMixin, DetailView):
    permission_required = "accounts.view_users"
    model = User
    template_name = "accounts/users/detail.html"
    context_object_name = "profile_user"

    def get_page_title(self):
        return str(self.object)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["can_manage"] = can_manage_user(self.request.user, self.object)
        context["is_self"] = self.object.pk == self.request.user.pk
        context["activities"] = ActivityLog.objects.filter(actor=self.object)[:10]
        context["breadcrumbs"] = [("Users", reverse("accounts:user_list"))]
        return context


class UserCreateView(ModulePermissionMixin, PageMetaMixin, CreateView):
    permission_required = "accounts.manage_users"
    model = User
    form_class = UserCreateForm
    template_name = "accounts/users/form.html"
    page_title = "Add User"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "actor": self.request.user}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["breadcrumbs"] = [("Users", reverse("accounts:user_list"))]
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.object
        log_activity(Action.USER_CREATED, f"Created {user.get_role_display()} account '{user.username}'",
                     request=self.request)
        if form.cleaned_data["password_method"] == UserCreateForm.EMAIL_INVITE:
            try:
                send_account_setup_email(self.request, user)
                messages.success(self.request, f"User '{user.username}' created. A setup link was emailed to {user.email}.")
            except Exception:
                messages.warning(self.request, f"User '{user.username}' created, but the setup email could not be sent.")
        else:
            messages.success(self.request, f"User '{user.username}' created successfully.")
        return response


class UserUpdateView(ManagedUserMixin, ModulePermissionMixin, PageMetaMixin, UpdateView):
    permission_required = "accounts.manage_users"
    form_class = UserUpdateForm
    template_name = "accounts/users/form.html"

    def get_object(self, queryset=None):
        return self.get_target()

    def get_page_title(self):
        return f"Edit {self.object}"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "actor": self.request.user}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["breadcrumbs"] = [
            ("Users", reverse("accounts:user_list")),
            (str(self.object), self.object.get_absolute_url()),
        ]
        return context

    def form_valid(self, form):
        old_role = User.objects.get(pk=self.object.pk).get_role_display()
        response = super().form_valid(form)
        user = self.object
        if "role" in form.changed_data:
            log_activity(Action.ROLE_CHANGED,
                         f"Changed role of '{user.username}' from {old_role} to {user.get_role_display()}",
                         request=self.request)
        log_activity(Action.USER_UPDATED, f"Updated account '{user.username}'", request=self.request)
        messages.success(self.request, f"User '{user.username}' updated.")
        return response


class UserToggleActiveView(ManagedUserMixin, ModulePermissionMixin, View):
    permission_required = "accounts.manage_users"
    http_method_names = ["post"]

    def post(self, request, pk):
        user = self.get_target()
        if user.pk == request.user.pk:
            messages.error(request, "You cannot deactivate your own account.")
            return redirect(user.get_absolute_url())
        user.is_active = not user.is_active
        user.save(update_fields=["is_active", "updated_at"])
        if user.is_active:
            log_activity(Action.USER_ACTIVATED, f"Activated account '{user.username}'", request=request)
            messages.success(request, f"'{user.username}' has been activated.")
        else:
            log_activity(Action.USER_DEACTIVATED, f"Deactivated account '{user.username}'", request=request)
            messages.success(request, f"'{user.username}' has been deactivated and can no longer sign in.")
        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
            return redirect(next_url)
        return redirect(user.get_absolute_url())


class UserSetPasswordView(ManagedUserMixin, ModulePermissionMixin, PageMetaMixin, FormView):
    permission_required = "accounts.manage_users"
    form_class = AdminSetPasswordForm
    template_name = "accounts/users/set_password.html"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.get_target()}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target = self.get_target()
        context.update(
            profile_user=target,
            page_title=f"Set password for {target}",
            breadcrumbs=[("Users", reverse("accounts:user_list")), (str(target), target.get_absolute_url())],
        )
        return context

    def form_valid(self, form):
        user = form.save()
        if user.pk == self.request.user.pk:
            update_session_auth_hash(self.request, user)
        log_activity(Action.PASSWORD_RESET, f"Set a new password for '{user.username}'", request=self.request)
        messages.success(self.request, f"Password updated for '{user.username}'.")
        return redirect(user.get_absolute_url())


class UserSendSetupEmailView(ManagedUserMixin, ModulePermissionMixin, View):
    permission_required = "accounts.manage_users"
    http_method_names = ["post"]

    def post(self, request, pk):
        user = self.get_target()
        try:
            send_account_setup_email(request, user)
        except Exception:
            messages.error(request, "The email could not be sent. Please check the email configuration.")
        else:
            log_activity(Action.PASSWORD_RESET, f"Sent a password setup link to '{user.username}'", request=request)
            messages.success(request, f"A password setup link was sent to {user.email}.")
        return redirect(user.get_absolute_url())


# --------------------------------------------------------------------------
# Roles & permissions (Super Admin only)
# --------------------------------------------------------------------------
# Super Admin always has everything; Member is portal-only (no staff permissions).
LOCKED_ROLES = (Role.SUPER_ADMIN, Role.MEMBER)


class RolePermissionsView(SuperAdminRequiredMixin, PageMetaMixin, TemplateView):
    template_name = "accounts/role_permissions.html"
    page_title = "Roles & Permissions"

    def editable_roles(self):
        return [r for r in Role if r not in LOCKED_ROLES]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        roles = list(Role)
        granted = {r: role_permission_codenames(r) for r in roles}
        modules = {}
        for codename, name, module in MODULE_PERMISSIONS:
            if codename in RESERVED_PERMISSIONS:
                continue
            cells = [
                {"role": r, "checked": r == Role.SUPER_ADMIN or (r != Role.MEMBER and codename in granted[r]),
                 "locked": r in LOCKED_ROLES}
                for r in roles
            ]
            modules.setdefault(module, []).append({"codename": codename, "name": name, "cells": cells})
        counts = dict(User.objects.order_by().values_list("role").annotate(n=Count("pk")))
        context["modules"] = modules
        context["role_columns"] = [
            {"value": r.value, "label": r.label, "count": counts.get(r.value, 0), "locked": r in LOCKED_ROLES}
            for r in roles
        ]
        return context

    def post(self, request):
        for role in self.editable_roles():
            set_role_permissions(role, request.POST.getlist(f"perm_{role.value}"))
        log_activity(Action.PERMISSIONS_UPDATED, "Updated role permissions", request=request)
        messages.success(request, "Role permissions saved.")
        return redirect("accounts:role_permissions")


# --------------------------------------------------------------------------
# Activity log
# --------------------------------------------------------------------------
class ActivityLogView(ModulePermissionMixin, PageMetaMixin, ListView):
    permission_required = "accounts.view_activity_log"
    template_name = "accounts/activity_log.html"
    context_object_name = "activities"
    paginate_by = 25
    page_title = "Activity Log"

    def get_queryset(self):
        qs = ActivityLog.objects.select_related("actor")
        q = self.request.GET.get("q", "").strip()
        action = self.request.GET.get("action", "")
        if q:
            qs = qs.filter(Q(description__icontains=q) | Q(actor__username__icontains=q))
        if action in Action.values:
            qs = qs.filter(action=action)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["actions"] = Action.choices
        context["filters"] = {k: self.request.GET.get(k, "") for k in ("q", "action")}
        context["is_filtered"] = any(context["filters"].values())
        return context
