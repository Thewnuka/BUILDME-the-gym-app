"""Effective gym settings: the in-app value when set, otherwise the server default.

One primary-key lookup per call (no process cache), so every worker sees a
change immediately.
"""
from django.conf import settings
from django.db import DatabaseError


def _row():
    from apps.core.models import GymSettings

    try:
        return GymSettings.objects.filter(pk=1).first()
    except DatabaseError:  # table not migrated yet
        return None


def get_settings():
    """The settings row (created on first use) for the edit form."""
    from apps.core.models import GymSettings

    obj, _ = GymSettings.objects.get_or_create(pk=1)
    return obj


def _pick(field, default):
    row = _row()
    value = getattr(row, field, None) if row else None
    return default if value in (None, "") else value


def gym_name():
    return _pick("gym_name", settings.GYM_NAME)


def contact():
    """Address and contact lines for receipts and printed reports."""
    row = _row()
    if not row:
        return {"address": "", "phone": "", "email": "", "website": "", "registration": "", "receipt_footer": ""}
    return {"address": row.address, "phone": row.phone, "email": row.email, "website": row.website,
            "registration": row.business_registration, "receipt_footer": row.receipt_footer}


def membership_expiry_reminder_days():
    raw = _pick("membership_expiry_reminder_days", "")
    if raw:
        return sorted({int(d) for d in str(raw).split(",") if d.strip().isdigit()}, reverse=True)
    return list(settings.MEMBERSHIP_EXPIRY_REMINDER_DAYS)


def payment_due_reminder_days():
    return _pick("payment_due_reminder_days", settings.PAYMENT_DUE_REMINDER_DAYS)


def expiring_soon_days():
    return _pick("expiring_soon_days", settings.MEMBERSHIP_EXPIRING_SOON_DAYS)


def equipment_maintenance_reminder_days():
    return _pick("equipment_maintenance_reminder_days", settings.EQUIPMENT_MAINTENANCE_REMINDER_DAYS)


def appointment_reminder_hours():
    return _pick("appointment_reminder_hours", settings.APPOINTMENT_REMINDER_HOURS)
