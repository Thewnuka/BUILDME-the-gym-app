from apps.accounts.models import User
from apps.accounts.roles import Role

PASSWORD = "Str0ng-Passw0rd!"


def make_user(role=Role.MEMBER, username=None, password=PASSWORD, **extra):
    username = username or f"{Role(role).value}_{User.objects.count() + 1}"
    extra.setdefault("email", f"{username}@example.com")
    extra.setdefault("first_name", username.title())
    extra.setdefault("last_name", "Test")
    user = User(username=username, role=role, **extra)
    user.set_password(password)
    user.save()
    return user
