from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core import gym
from apps.core.models import ActivityLog, Notification
from apps.core.notify import notify_staff
from apps.core.utils import log_activity
from apps.expenses.models import Expense, ExpenseCategory
from apps.expenses.services import create_linked_expense

from .models import Equipment, FaultReport, MaintenanceRecord

Action = ActivityLog.Action


class EquipmentError(Exception):
    pass


def next_code():
    n = Equipment.objects.count() + 1
    while Equipment.objects.filter(code=f"EQ-{n:04d}").exists():
        n += 1
    return f"EQ-{n:04d}"


@transaction.atomic
def register_equipment(form, actor, record_expense=True, request=None):
    """Save new equipment; a purchase cost becomes one linked Equipment expense (approved policy)."""
    equipment = form.save(commit=False)
    equipment.code = (equipment.code or "").strip().upper() or next_code()
    equipment.created_by = actor
    if equipment.maintenance_interval_days and not equipment.next_maintenance_date:
        start = equipment.purchase_date or timezone.localdate()
        equipment.next_maintenance_date = start + timedelta(days=equipment.maintenance_interval_days)
    if record_expense and equipment.purchase_cost:
        equipment.purchase_expense = create_linked_expense(
            source=Expense.Source.EQUIPMENT, category=ExpenseCategory.EQUIPMENT, amount=equipment.purchase_cost,
            expense_date=equipment.purchase_date or timezone.localdate(), supplier=equipment.supplier,
            description=f"Purchase of {equipment.code} {equipment.name}", actor=actor,
        )
    equipment.save()
    log_activity(Action.EQUIPMENT_UPDATED, f"Registered equipment {equipment.code} ({equipment.name})",
                 actor=actor, request=request)
    return equipment


@transaction.atomic
def report_fault(equipment, description, severity, actor, take_out_of_service=False, request=None):
    equipment = Equipment.objects.select_for_update().get(pk=equipment.pk)
    fault = FaultReport.objects.create(equipment=equipment, description=description.strip(), severity=severity,
                                       reported_by=actor)
    if severity == FaultReport.Severity.UNSAFE or take_out_of_service:
        equipment.status = Equipment.Status.OUT_OF_SERVICE
        equipment.save(update_fields=["status", "updated_at"])
    log_activity(Action.EQUIPMENT_FAULT, f"Fault reported on {equipment.code}: {fault.get_severity_display()}",
                 actor=actor, request=request)
    notify_staff("accounts.manage_equipment", title=f"Fault reported: {equipment.code} {equipment.name}",
                 message=f"{fault.get_severity_display()}. {description[:200]}",
                 link=equipment.get_absolute_url(), category=Notification.Category.EQUIPMENT,
                 dedupe_key=f"fault:{fault.pk}")
    return fault


@transaction.atomic
def record_maintenance(equipment, form, actor, status_after, resolve_fault=None, request=None):
    """Log maintenance/repair. A cost creates one linked Maintenance expense."""
    equipment = Equipment.objects.select_for_update().get(pk=equipment.pk)
    record = form.save(commit=False)
    record.equipment = equipment
    record.recorded_by = actor
    if resolve_fault is not None:
        if resolve_fault.equipment_id != equipment.pk:
            raise EquipmentError("That fault belongs to different equipment.")
        record.fault = resolve_fault
    record.expense = create_linked_expense(
        source=Expense.Source.MAINTENANCE, category=ExpenseCategory.MAINTENANCE, amount=record.cost,
        expense_date=record.performed_on, supplier=record.supplier,
        description=f"{record.get_kind_display()} of {equipment.code}: {record.description}", actor=actor,
    )
    record.save()
    if resolve_fault is not None and resolve_fault.status == FaultReport.Status.OPEN:
        resolve_fault.status = FaultReport.Status.RESOLVED
        resolve_fault.resolved_at = timezone.now()
        resolve_fault.resolution = record.description[:255]
        resolve_fault.save(update_fields=["status", "resolved_at", "resolution"])
    if record.kind in (MaintenanceRecord.Kind.SERVICE, MaintenanceRecord.Kind.REPAIR) and equipment.maintenance_interval_days:
        equipment.next_maintenance_date = record.performed_on + timedelta(days=equipment.maintenance_interval_days)
    if status_after in Equipment.Status.values:
        equipment.status = status_after
    equipment.save()
    log_activity(Action.EQUIPMENT_MAINTAINED, f"{record.get_kind_display()} recorded for {equipment.code}",
                 actor=actor, request=request)
    return record


@transaction.atomic
def set_status(equipment, status, actor, request=None):
    if status not in Equipment.Status.values:
        raise EquipmentError("Unknown status.")
    equipment = Equipment.objects.select_for_update().get(pk=equipment.pk)
    old = equipment.get_status_display()
    equipment.status = status
    equipment.save(update_fields=["status", "updated_at"])
    log_activity(Action.EQUIPMENT_UPDATED, f"{equipment.code} status: {old} → {equipment.get_status_display()}",
                 actor=actor, request=request)
    return equipment


def maintenance_due(days=None):
    
    days = gym.equipment_maintenance_reminder_days() if days is None else days
    return Equipment.objects.exclude(status=Equipment.Status.OUT_OF_SERVICE).filter(
        next_maintenance_date__isnull=False, next_maintenance_date__lte=timezone.localdate() + timedelta(days=days))


def send_maintenance_reminders():
    """One reminder per equipment per due date (re-armed when the date moves)."""
    sent = 0
    for eq in maintenance_due():
        overdue = eq.next_maintenance_date < timezone.localdate()
        sent += notify_staff(
            "accounts.manage_equipment",
            title=f"Maintenance {'overdue' if overdue else 'due'}: {eq.code} {eq.name}",
            message=f"Next maintenance date {eq.next_maintenance_date:%d %b %Y}.",
            link=reverse("equipment:detail", args=[eq.pk]), category=Notification.Category.EQUIPMENT,
            dedupe_key=f"maintenance_due:{eq.pk}:{eq.next_maintenance_date.isoformat()}",
        )
    return sent
