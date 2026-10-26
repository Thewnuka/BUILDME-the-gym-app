"""
Approved fix: the Member role is portal-only.

On 25 Oct 2026 the Member role was given staff permissions (view_activity_log,
view_attendance, view_training) via Roles & Permissions, which let any member
see other members' data. This removes every module permission from the Member
group. Members keep their self-service portal, which needs no permissions.
Reversing does not restore the removed grants (they were a privacy leak).
"""
from django.db import migrations


def clear_member_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for group in Group.objects.filter(name="Member"):
        group.permissions.clear()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_training_permissions"),
    ]

    operations = [migrations.RunPython(clear_member_permissions, migrations.RunPython.noop)]
