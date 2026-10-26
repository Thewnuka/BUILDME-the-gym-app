from django.db.models import Q
from django.utils import timezone

from apps.dashboard.registry import register

from .models import Equipment, FaultReport


@register("equipment_attention")
def equipment_attention(user):
    today = timezone.localdate()
    qs = Equipment.objects.filter(
        Q(faults__status=FaultReport.Status.OPEN) | Q(next_maintenance_date__lte=today)
        | Q(status__in=[Equipment.Status.UNDER_MAINTENANCE, Equipment.Status.OUT_OF_SERVICE])).distinct()
    out = Equipment.objects.filter(status=Equipment.Status.OUT_OF_SERVICE).count()
    return {"value": qs.count(), "note": f"{out} out of service"}
