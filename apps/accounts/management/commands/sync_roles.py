from django.core.management.base import BaseCommand

from apps.accounts.models import User
from apps.accounts.services import ensure_role_groups


class Command(BaseCommand):
    help = "Create role groups and (optionally) reset their permissions to the defaults."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true", help="Overwrite customised role permissions with the defaults."
        )

    def handle(self, *args, **options):
        ensure_role_groups(reset=options["reset"])
        for user in User.objects.all():
            user.sync_role_group()
        self.stdout.write(self.style.SUCCESS("Role groups are in sync."))
