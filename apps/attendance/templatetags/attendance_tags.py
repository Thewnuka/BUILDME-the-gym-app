from datetime import timedelta

from django import template
from django.utils import timezone

from apps.attendance.qr import qr_svg
from apps.attendance.services import get_pass

register = template.Library()


@register.inclusion_tag("attendance/_member_panel.html", takes_context=True)
def member_attendance(context, member, limit=8):
    user = context["request"].user
    is_self = member.user_id is not None and member.user_id == user.pk
    visible = user.has_perm("accounts.view_attendance") or is_self
    visits = member.visits.select_related("membership").order_by("-checked_in_at") if visible else None
    since = timezone.now() - timedelta(days=30)
    return {
        "member": member,
        "visible": visible,
        "visits": visits[:limit] if visible else [],
        "last_30_days": visits.filter(checked_in_at__gte=since).count() if visible else 0,
        "total": visits.count() if visible else 0,
        "can_view_all": user.has_perm("accounts.view_attendance"),
        "can_check_in": user.has_perm("accounts.manage_attendance"),
        "can_view_pass": is_self or user.has_perm("accounts.manage_attendance"),
    }


@register.simple_tag
def checkin_qr(member, scale=5):
    return qr_svg(get_pass(member).qr_payload, scale=scale)
