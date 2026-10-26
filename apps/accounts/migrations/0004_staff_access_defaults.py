"""
Approved access changes applied to existing role groups:
- Gym Manager may record and confirm payments (manage_payments).
- Receptionist may assign, renew, suspend and cancel memberships (manage_memberships).

Only these two grants are added. Other customisations made in Roles &
Permissions are left as they are. Reversing removes just these two grants.
"""
from django.db import migrations

GRANTS = {"Gym Manager": "manage_payments", "Receptionist": "manage_memberships"}


def _permission(apps, codename):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    ct, _ = ContentType.objects.get_or_create(app_label="accounts", model="user")
    perm, _ = Permission.objects.get_or_create(content_type=ct, codename=codename, defaults={"name": codename})
    return perm


def grant(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for group_name, codename in GRANTS.items():
        for group in Group.objects.filter(name=group_name):
            group.permissions.add(_permission(apps, codename))


def revoke(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for group_name, codename in GRANTS.items():
        for group in Group.objects.filter(name=group_name):
            group.permissions.remove(_permission(apps, codename))


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_refund_permission"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(grant, revoke)]
