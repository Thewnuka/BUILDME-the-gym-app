from django.conf import settings
from django.urls import reverse

from . import gym

# Sidebar definition. Items are shown only to users holding ``perm``.
# ``url_name=None`` marks modules scheduled for later phases.
NAVIGATION = [
    {
        "title": "Main",
        "items": [
            {"label": "Dashboard", "icon": "speedometer2", "url_name": "dashboard:home", "perm": None},
        ],
    },
    {
        "title": "Gym",
        "items": [
            {"label": "Members", "icon": "people", "url_name": "members:list", "perm": "accounts.view_members"},
            {"label": "Memberships", "icon": "card-checklist", "url_name": "memberships:list",
             "perm": "accounts.view_memberships"},
            {"label": "Membership Plans", "icon": "tags", "url_name": "memberships:plan_list",
             "perm": "accounts.view_memberships"},
            {"label": "Check-in Desk", "icon": "door-open", "url_name": "attendance:desk",
             "perm": "accounts.manage_attendance"},
            {"label": "Attendance", "icon": "calendar-check", "url_name": "attendance:list",
             "perm": "accounts.view_attendance"},
        ],
    },
    {
        "title": "Training",
        "items": [
            {"label": "Trainers", "icon": "person-badge", "url_name": "trainers:list", "perm": "accounts.view_training"},
            {"label": "Appointments", "icon": "calendar-week", "url_name": "appointments:list",
             "perm": "accounts.view_training"},
            {"label": "Workout Plans", "icon": "lightning-charge", "url_name": "workouts:plans",
             "perm": "accounts.view_training"},
            {"label": "Exercise Library", "icon": "collection", "url_name": "workouts:exercises",
             "perm": "accounts.view_training"},
            {"label": "Meal Plans", "icon": "egg-fried", "url_name": "nutrition:plans", "perm": "accounts.view_training",
             "nutrition": True},
        ],
    },
    {
        "title": "Finance",
        "items": [
            {"label": "Payments", "icon": "credit-card", "url_name": "payments:list", "perm": "accounts.view_payments"},
            {"label": "Expenses", "icon": "receipt-cutoff", "url_name": "expenses:list", "perm": "accounts.view_expenses"},
            {"label": "Reports", "icon": "file-earmark-bar-graph", "url_name": "reports:index", "perm": "accounts.view_reports"},
        ],
    },
    {
        "title": "Operations",
        "items": [
            {"label": "Equipment", "icon": "tools", "url_name": "equipment:list", "perm": "accounts.report_equipment_faults"},
            {"label": "Inventory", "icon": "box-seam", "url_name": "inventory:list", "perm": "accounts.use_inventory"},
            {"label": "Announcements", "icon": "megaphone", "url_name": "announcements:manage",
             "perm": "accounts.manage_announcements"},
        ],
    },
    {
        "title": "Administration",
        "items": [
            {"label": "Users", "icon": "person-gear", "url_name": "accounts:user_list", "perm": "accounts.view_users"},
            {"label": "Roles & Permissions", "icon": "shield-lock", "url_name": "accounts:role_permissions",
             "perm": "accounts.manage_roles"},
            {"label": "Activity Log", "icon": "clock-history", "url_name": "accounts:activity_log",
             "perm": "accounts.view_activity_log"},
            {"label": "Message Log", "icon": "send-check", "url_name": "notifications:deliveries",
             "perm": "accounts.view_notification_log"},
            {"label": "Gym Settings", "icon": "sliders", "url_name": "settings", "perm": "accounts.manage_settings"},
        ],
    },
    {
        "title": "My Training",
        "items": [
            {"label": "My Training", "icon": "lightning-charge", "url_name": "workouts:my", "perm": None, "member": True},
            {"label": "My Sessions", "icon": "calendar-week", "url_name": "appointments:my_list", "perm": None,
             "member": True},
            {"label": "My Progress", "icon": "graph-up", "url_name": "workouts:my_progress", "perm": None, "member": True},
            {"label": "My Nutrition", "icon": "egg-fried", "url_name": "nutrition:my", "perm": None, "member": True,
             "nutrition": True},
        ],
    },
    {
        "title": "Account",
        "items": [
            {"label": "Notices", "icon": "megaphone", "url_name": "announcements:board", "perm": None},
            {"label": "Notifications", "icon": "bell", "url_name": "notifications:list", "perm": None},
            {"label": "My Profile", "icon": "person-circle", "url_name": "accounts:profile", "perm": None},
            {"label": "Change Password", "icon": "key", "url_name": "accounts:password_change", "perm": None},
        ],
    },
]


def site(request):
    return {
        "GYM_NAME": gym.gym_name(),
        "CURRENCY_SYMBOL": settings.CURRENCY_SYMBOL,
        "REGISTRATION_ENABLED": settings.REGISTRATION_ENABLED,
    }


def navigation(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    path = request.path
    sections = []
    for section in NAVIGATION:
        items = []
        for item in section["items"]:
            if item["perm"] and not user.has_perm(item["perm"]):
                continue
            if item.get("member") and getattr(user, "member_profile", None) is None:
                continue
            if item.get("nutrition") and not settings.NUTRITION_ENABLED:
                continue
            url = reverse(item["url_name"]) if item["url_name"] else None
            items.append({**item, "url": url, "active": False, "coming_soon": url is None})
        if items:
            sections.append({"title": section["title"], "items": items})
    # Highlight the most specific matching link (e.g. /memberships/plans/
    # over /memberships/).
    matches = [
        item for section in sections for item in section["items"]
        if item["url"] and (path == "/" if item["url"] == "/" else path.startswith(item["url"]))
    ]
    if matches:
        max(matches, key=lambda item: len(item["url"]))["active"] = True
    from .models import Notification

    unread = Notification.objects.filter(user=user, read_at__isnull=True).count()
    return {"nav_sections": sections, "unread_notifications": unread}
