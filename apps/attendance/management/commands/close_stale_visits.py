from django.core.management.base import BaseCommand

from apps.attendance.services import close_stale_visits


class Command(BaseCommand):
    help = "Mark visits left open from previous days as 'no check-out recorded'. Safe to run nightly."

    def handle(self, *args, **options):
        count = close_stale_visits()
        self.stdout.write(self.style.SUCCESS(f"Closed {count} stale visit(s)."))
