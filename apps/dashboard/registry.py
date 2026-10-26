"""
Dashboard metric registry.

The dashboard declares every statistic it can show (a *slot*). Feature
modules register a provider function for a slot from their ``AppConfig.ready``
(see ``apps/members/metrics.py``). Slots without a provider render as
"pending" until the module that owns the data is installed, so no
hard-coded demonstration numbers are ever displayed.

A provider registered later for the same key replaces an earlier one.
"""
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Slot:
    key: str
    label: str
    icon: str
    color: str
    permission: str
    module: str
    fmt: str = "number"  # number | currency


STAT_SLOTS = [
    Slot("total_members", "Total Members", "people-fill", "primary", "accounts.view_members", "Members"),
    Slot("active_members", "Active Members", "person-check-fill", "success", "accounts.view_memberships", "Memberships"),
    Slot("expired_memberships", "Expired Memberships", "hourglass-bottom", "danger",
         "accounts.view_memberships", "Memberships"),
    Slot("new_members_month", "New This Month", "person-plus-fill", "info", "accounts.view_members", "Members"),
    Slot("today_attendance", "Today's Attendance", "calendar-check-fill", "teal",
         "accounts.view_attendance", "Attendance"),
    Slot("today_revenue", "Collected Today", "cash-coin", "success", "accounts.view_payments", "Payments", "currency"),
    Slot("monthly_revenue", "Collected This Month", "graph-up-arrow", "primary",
         "accounts.view_payments", "Payments", "currency"),
    Slot("outstanding_payments", "Outstanding (Unpaid)", "exclamation-diamond-fill", "warning",
         "accounts.view_payments", "Payments", "currency"),
    Slot("total_trainers", "Trainers", "person-badge-fill", "info", "accounts.view_training", "Training"),
    Slot("upcoming_appointments", "Upcoming Sessions", "calendar-week", "primary", "accounts.view_training", "Training"),
    Slot("completed_sessions", "Sessions Completed", "calendar-check", "success", "accounts.view_training",
         "Training"),
    Slot("workout_plans_assigned", "Workout Plans Active", "lightning-charge-fill", "teal", "accounts.view_training",
         "Training"),
    Slot("expenses_month", "Expenses This Month", "receipt-cutoff", "danger", "accounts.view_expenses", "Expenses",
         "currency"),
    Slot("profit_month", "Est. Operating Profit", "piggy-bank", "success", "accounts.view_expenses", "Expenses",
         "currency"),
    Slot("equipment_attention", "Equipment Needing Attention", "tools", "warning", "accounts.report_equipment_faults",
         "Equipment"),
    Slot("low_stock", "Low-Stock Items", "box-seam", "danger", "accounts.use_inventory", "Inventory"),
]

PANEL_SLOTS = {
    "member_growth": Slot("member_growth", "New Members (last 6 months)", "bar-chart-line", "primary",
                          "accounts.view_members", "Members"),
    "revenue_trend": Slot("revenue_trend", "Revenue collected (last 6 months)", "graph-up", "success",
                          "accounts.view_payments", "Payments"),
    "upcoming_expirations": Slot("upcoming_expirations", "Upcoming Expirations", "calendar-x", "warning",
                                 "accounts.view_memberships", "Memberships"),
    "recent_activity": Slot("recent_activity", "Recent Activities", "activity", "info",
                            "accounts.view_activity_log", "Activity Log"),
    "recent_payments": Slot("recent_payments", "Recent Payments", "receipt", "success",
                            "accounts.view_payments", "Payments"),
    "payment_breakdown": Slot("payment_breakdown", "Membership Payment Status", "pie-chart", "primary",
                              "accounts.view_payments", "Payments"),
    "attendance_today": Slot("attendance_today", "Today's Check-ins", "clock", "teal",
                             "accounts.view_attendance", "Attendance"),
    "revenue_vs_expenses": Slot("revenue_vs_expenses", "Revenue vs expenses (last 6 months)", "bar-chart-steps",
                                "primary", "accounts.view_expenses", "Expenses"),
}

_providers = {}


def register(key):
    """Decorator registering ``func(user)`` as the data provider for ``key``."""
    known = {s.key for s in STAT_SLOTS} | set(PANEL_SLOTS)
    if key not in known:
        raise KeyError(f"Unknown dashboard slot: {key}")

    def decorator(func):
        _providers[key] = func
        return func

    return decorator


def _resolve(slot, user):
    provider = _providers.get(slot.key)
    result = {"slot": slot, "status": "pending", "value": None, "note": ""}
    if provider is None:
        return result
    try:
        value = provider(user)
        # A stat provider may return {"value": ..., "note": "..."} for a sub-line.
        if isinstance(value, dict) and "value" in value:
            result["note"] = value.get("note", "")
            value = value["value"]
        result["value"] = value
        result["status"] = "ready"
    except Exception:  # A broken widget must not take the whole dashboard down.
        logger.exception("Dashboard provider %s failed", slot.key)
        result["status"] = "error"
    return result


def stats_for(user):
    return [_resolve(slot, user) for slot in STAT_SLOTS if user.has_perm(slot.permission)]


def panels_for(user):
    return {key: _resolve(slot, user) for key, slot in PANEL_SLOTS.items() if user.has_perm(slot.permission)}
