from django.core.management.base import BaseCommand

from apps.core.scheduled import run_all


class Command(BaseCommand):
    help = ("Run all recurring jobs (reminders, low stock, maintenance, scheduled announcements). "
            "Safe to run as often as you like: reminders are never duplicated.")

    def handle(self, *args, **options):
        for name, result in run_all().items():
            self.stdout.write(f"{name}: {result}")
        self.stdout.write(self.style.SUCCESS("Scheduled tasks finished."))
