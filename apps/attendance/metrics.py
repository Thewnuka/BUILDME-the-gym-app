from datetime import datetime, time, timedelta

from django.utils import timezone

from apps.dashboard.registry import register

from .models import Attendance


def _today_visits():
    start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    return Attendance.objects.filter(checked_in_at__gte=start, checked_in_at__lt=start + timedelta(days=1))


@register("today_attendance")
def today_attendance(user):
    visits = _today_visits()
    in_gym = visits.filter(checked_out_at__isnull=True, auto_closed=False).count()
    members = visits.values("member").distinct().count()
    return {"value": visits.count(), "note": f"{members} member{'s' if members != 1 else ''} · {in_gym} in the gym now"}


@register("attendance_today")
def attendance_today(user):
    hourly = {}
    for checked_in_at in _today_visits().values_list("checked_in_at", flat=True):
        hour = timezone.localtime(checked_in_at).hour
        hourly[hour] = hourly.get(hour, 0) + 1
    hours = range(min(list(hourly) + [6]), max(list(hourly) + [21]) + 1)
    return {"labels": [f"{h:02d}:00" for h in hours], "data": [hourly.get(h, 0) for h in hours]}
