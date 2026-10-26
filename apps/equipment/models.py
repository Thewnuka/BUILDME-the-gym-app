from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone


class Equipment(models.Model):
    class Category(models.TextChoices):
        CARDIO = "cardio", "Cardio machines"
        STRENGTH = "strength", "Strength machines"
        FREE_WEIGHTS = "free_weights", "Free weights & racks"
        ACCESSORIES = "accessories", "Accessories"
        FACILITY = "facility", "Facility (AC, showers, lockers…)"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        IN_USE = "in_use", "In use"
        UNDER_MAINTENANCE = "under_maintenance", "Under maintenance"
        OUT_OF_SERVICE = "out_of_service", "Out of service"

    class Condition(models.TextChoices):
        EXCELLENT = "excellent", "Excellent"
        GOOD = "good", "Good"
        FAIR = "fair", "Fair"
        POOR = "poor", "Poor"

    STATUS_BADGES = {"available": "success", "in_use": "primary", "under_maintenance": "warning",
                     "out_of_service": "danger"}

    code = models.CharField("equipment code", max_length=30, unique=True, help_text="Leave blank to auto-generate.")
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=15, choices=Category.choices, db_index=True)
    brand_model = models.CharField("brand / model", max_length=150, blank=True)
    serial_number = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=100, blank=True)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField("purchase cost (LKR)", max_digits=12, decimal_places=2, null=True, blank=True)
    supplier = models.ForeignKey("expenses.Supplier", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="equipment")
    purchase_expense = models.OneToOneField("expenses.Expense", null=True, blank=True, on_delete=models.PROTECT,
                                            related_name="purchased_equipment", editable=False)
    warranty_expires = models.DateField("warranty until", null=True, blank=True)
    warranty_notes = models.CharField(max_length=255, blank=True)
    condition = models.CharField(max_length=10, choices=Condition.choices, default=Condition.GOOD)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE, db_index=True)
    maintenance_interval_days = models.PositiveSmallIntegerField(
        "service every (days)", null=True, blank=True, help_text="Sets the next maintenance date after each service.")
    next_maintenance_date = models.DateField(null=True, blank=True, db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "equipment"
        constraints = [
            models.CheckConstraint(condition=Q(purchase_cost__isnull=True) | Q(purchase_cost__gte=0),
                                   name="equipment_cost_not_negative"),
            models.CheckConstraint(condition=Q(maintenance_interval_days__isnull=True)
                                   | Q(maintenance_interval_days__gte=1), name="equipment_interval_positive"),
        ]

    def __str__(self):
        return f"{self.code} · {self.name}"

    def get_absolute_url(self):
        return reverse("equipment:detail", args=[self.pk])

    @property
    def status_badge(self):
        return self.STATUS_BADGES.get(self.status, "secondary")

    @property
    def under_warranty(self):
        return bool(self.warranty_expires and self.warranty_expires >= timezone.localdate())

    @property
    def maintenance_overdue(self):
        return bool(self.next_maintenance_date and self.next_maintenance_date < timezone.localdate())

    @property
    def open_faults(self):
        return self.faults.filter(status=FaultReport.Status.OPEN)


class FaultReport(models.Model):
    class Severity(models.TextChoices):
        LOW = "low", "Low: cosmetic or minor"
        MEDIUM = "medium", "Medium: works, needs attention"
        HIGH = "high", "High: barely usable"
        UNSAFE = "unsafe", "Unsafe: stop using now"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    SEVERITY_BADGES = {"low": "secondary", "medium": "info", "high": "warning", "unsafe": "danger"}

    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name="faults")
    description = models.TextField()
    severity = models.CharField(max_length=8, choices=Severity.choices, default=Severity.MEDIUM)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN, db_index=True)
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"Fault on {self.equipment.code}: {self.get_severity_display()}"

    @property
    def severity_badge(self):
        return self.SEVERITY_BADGES.get(self.severity, "secondary")


class MaintenanceRecord(models.Model):
    class Kind(models.TextChoices):
        REPAIR = "repair", "Repair"
        SERVICE = "service", "Scheduled service"
        INSPECTION = "inspection", "Inspection"

    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, related_name="maintenance_records")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    performed_on = models.DateField(default=timezone.localdate)
    description = models.CharField(max_length=255)
    cost = models.DecimalField("cost (LKR)", max_digits=12, decimal_places=2, null=True, blank=True)
    performed_by = models.CharField("technician / company", max_length=150, blank=True)
    supplier = models.ForeignKey("expenses.Supplier", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="maintenance_jobs")
    fault = models.ForeignKey(FaultReport, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="repairs", help_text="The fault this work fixed, if any.")
    expense = models.OneToOneField("expenses.Expense", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="maintenance_record", editable=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-performed_on", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(cost__isnull=True) | Q(cost__gte=0),
                                              name="maintenance_cost_not_negative")]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.equipment.code} · {self.performed_on}"
