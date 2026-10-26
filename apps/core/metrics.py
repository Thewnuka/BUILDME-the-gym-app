from apps.dashboard.registry import register

from .models import ActivityLog


@register("recent_activity")
def recent_activity(user, limit=8):
    return list(ActivityLog.objects.select_related("actor")[:limit])
