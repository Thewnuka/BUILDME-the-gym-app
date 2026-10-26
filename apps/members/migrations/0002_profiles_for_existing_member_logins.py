"""
Additive data migration: give every existing Member-role login a member
profile so Phase 1 accounts appear in member management. No existing rows
are modified or deleted. Reversing it removes only the profiles it created.
"""
from django.conf import settings
from django.db import migrations


def create_profiles(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Member = apps.get_model("members", "Member")
    taken_emails = {e.lower() for e in Member.objects.exclude(email="").values_list("email", flat=True)}
    for user in User.objects.filter(role="member", member_profile__isnull=True).order_by("pk"):
        email = (user.email or "").lower()
        if email in taken_emails:
            email = ""
        full_name = " ".join(filter(None, [user.first_name, user.last_name])) or user.username
        member = Member.objects.create(
            user=user, full_name=full_name, email=email,
            joined_on=user.date_joined.date(), fitness_goals="", deactivation_reason="",
            status="active" if user.is_active else "inactive",
            deactivated_on=None if user.is_active else user.date_joined.date(),
        )
        # Historical models don't run Member.save(), so derive the ID here.
        member.member_id = f"{settings.MEMBER_ID_PREFIX}-{member.pk:06d}"
        member.save(update_fields=["member_id"])
        if email:
            taken_emails.add(email)


def remove_profiles(apps, schema_editor):
    # Only profiles still linked to a login and never given staff-entered
    # details beyond what this migration set are removed.
    Member = apps.get_model("members", "Member")
    Member.objects.filter(user__isnull=False, phone="", address="", memberships__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0001_initial"),
        ("accounts", "0002_plan_permissions"),
        ("memberships", "0001_initial"),
    ]

    operations = [migrations.RunPython(create_profiles, remove_profiles)]
