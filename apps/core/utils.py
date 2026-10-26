from django.core.paginator import Paginator


def get_client_ip(request):
    """Best-effort client IP. REMOTE_ADDR is trusted; set up the proxy to populate it."""
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR") or None


def log_activity(action, description, actor=None, request=None):
    from .models import ActivityLog

    if actor is None and request is not None and request.user.is_authenticated:
        actor = request.user
    return ActivityLog.objects.create(
        actor=actor if actor and actor.pk else None,
        action=action,
        description=description[:255],
        ip_address=get_client_ip(request),
    )


def paginate(request, queryset, per_page=15):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page"))
