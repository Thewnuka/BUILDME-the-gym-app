"""
Role and permission catalogue.

Each role is backed by a Django ``Group`` whose permissions can be edited by
the Super Admin from the "Roles & Permissions" screen. The defaults below are
applied when a group is first created (and by ``manage.py sync_roles --reset``).

Module permissions are declared as custom permissions on the ``User`` model
(app label ``accounts``) so they exist before the modules that use them are
built in later phases.
"""
from django.db import models


class Role(models.TextChoices):
    SUPER_ADMIN = "super_admin", "Super Admin"
    GYM_MANAGER = "gym_manager", "Gym Manager"
    RECEPTIONIST = "receptionist", "Receptionist"
    TRAINER = "trainer", "Trainer"
    ACCOUNTANT = "accountant", "Accountant"
    MEMBER = "member", "Member"


# Higher rank = more authority. A user may only manage users and assign roles
# that rank strictly below their own (the Super Admin may manage everyone).
ROLE_RANK = {
    Role.SUPER_ADMIN: 100,
    Role.GYM_MANAGER: 80,
    Role.ACCOUNTANT: 50,
    Role.RECEPTIONIST: 50,
    Role.TRAINER: 50,
    Role.MEMBER: 10,
}

STAFF_ROLES = [r for r in Role if r != Role.MEMBER]

ROLE_BADGES = {
    Role.SUPER_ADMIN: "danger",
    Role.GYM_MANAGER: "primary",
    Role.RECEPTIONIST: "info",
    Role.TRAINER: "success",
    Role.ACCOUNTANT: "warning",
    Role.MEMBER: "secondary",
}

# (codename, human readable name, module label)
MODULE_PERMISSIONS = [
    ("view_dashboard", "Can view the administrative dashboard", "Dashboard"),
    ("view_users", "Can view user accounts", "Users"),
    ("manage_users", "Can create, edit, activate and deactivate users", "Users"),
    ("view_members", "Can view members", "Members"),
    ("manage_members", "Can create and edit members", "Members"),
    ("view_memberships", "Can view memberships and plans", "Memberships"),
    ("manage_memberships", "Can assign, renew, suspend and cancel memberships", "Memberships"),
    ("manage_plans", "Can create and edit membership plans", "Memberships"),
    ("change_plan_prices", "Can set and change membership plan prices", "Memberships"),
    ("view_attendance", "Can view attendance", "Attendance"),
    ("manage_attendance", "Can record attendance (check-in/out)", "Attendance"),
    ("view_payments", "Can view payments and revenue", "Payments"),
    ("manage_payments", "Can record and confirm payments", "Payments"),
    ("refund_payments", "Can record refunds, balance adjustments and void payments", "Payments"),
    ("view_training", "Can view trainers, workout plans and training schedules", "Training"),
    ("manage_training", "Can create workout plans, meal plans and progress records", "Training"),
    ("manage_trainers", "Can manage trainer profiles, availability and assignments", "Training"),
    ("manage_appointments", "Can book and manage training appointments for any member", "Training"),
    ("view_member_health", "Can view every member's fitness and nutrition records", "Training"),
    ("view_trainer_pay", "Can view and record trainer salary and commission", "Training"),
    ("view_reports", "Can view reports", "Reports"),
    ("view_expenses", "Can view expenses and profit figures", "Expenses"),
    ("manage_expenses", "Can record, edit and void expenses", "Expenses"),
    ("manage_equipment", "Can register equipment and record maintenance and repairs", "Equipment"),
    ("report_equipment_faults", "Can view equipment and report faults", "Equipment"),
    ("manage_inventory", "Can manage inventory items, purchases and adjustments", "Inventory"),
    ("use_inventory", "Can view stock and record items used", "Inventory"),
    ("manage_announcements", "Can create and publish announcements", "Announcements"),
    ("view_notification_log", "Can view the notification delivery log", "Announcements"),
    ("view_activity_log", "Can view the activity log", "Activity Log"),
    ("manage_settings", "Can change gym details and reminder settings", "Settings"),
    ("manage_roles", "Can manage roles and permissions", "Roles"),
]

# Permissions that can never be granted through the role matrix: only the
# Super Admin (a Django superuser) holds them. This prevents a role from
# being used to escalate its own privileges.
RESERVED_PERMISSIONS = {"manage_roles"}

DEFAULT_ROLE_PERMISSIONS = {
    Role.SUPER_ADMIN: [p[0] for p in MODULE_PERMISSIONS],
    Role.GYM_MANAGER: [
        "view_dashboard", "view_users", "manage_users",
        "view_members", "manage_members", "view_memberships", "manage_memberships",
        "manage_plans", "change_plan_prices",
        "view_attendance", "manage_attendance", "view_payments", "manage_payments", "refund_payments",
        "view_training", "manage_training", "manage_trainers", "manage_appointments", "view_member_health",
        "view_reports", "view_activity_log", "view_expenses", "manage_equipment", "report_equipment_faults",
        "manage_inventory", "use_inventory", "manage_announcements", "view_notification_log",
    ],
    Role.RECEPTIONIST: [
        "view_dashboard", "view_members", "manage_members", "view_memberships", "manage_memberships",
        "view_attendance", "manage_attendance", "view_payments", "manage_payments",
        "view_training", "manage_appointments", "report_equipment_faults", "use_inventory",
    ],
    Role.TRAINER: [
        "view_dashboard", "view_members", "view_attendance",
        "view_training", "manage_training", "report_equipment_faults", "use_inventory",
    ],
    Role.ACCOUNTANT: [
        "view_dashboard", "view_members", "view_memberships",
        "view_payments", "manage_payments", "view_reports", "view_trainer_pay",
        "view_expenses", "manage_expenses", "report_equipment_faults", "use_inventory",
    ],
    Role.MEMBER: [],
}


def group_name(role):
    return Role(role).label


def role_rank(role):
    return ROLE_RANK.get(role, 0)


def assignable_roles(user):
    """Roles that ``user`` is allowed to give to other accounts."""
    if not user.is_authenticated:
        return []
    if user.is_super_admin:
        return list(Role)
    own = role_rank(user.role)
    return [r for r in Role if role_rank(r) < own]


def can_manage_user(actor, target):
    """Whether ``actor`` may edit/activate/deactivate ``target``."""
    if not actor.is_authenticated or not actor.has_perm("accounts.manage_users"):
        return False
    if actor.is_super_admin:
        return True
    return role_rank(target.role) < role_rank(actor.role)
