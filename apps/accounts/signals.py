from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from apps.core.models import ActivityLog
from apps.core.utils import log_activity


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_activity(ActivityLog.Action.LOGIN, f"{user.username} signed in", actor=user, request=request)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_activity(ActivityLog.Action.LOGOUT, f"{user.username} signed out", actor=user, request=request)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    # Only the submitted identifier is recorded, never the password.
    attempted = str(credentials.get("username", ""))[:80]
    log_activity(ActivityLog.Action.LOGIN_FAILED, f"Failed sign-in attempt for '{attempted}'", request=request)
