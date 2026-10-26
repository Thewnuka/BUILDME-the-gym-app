from django.core.management.base import BaseCommand

from apps.appointments.services import send_reminders


class Command(BaseCommand):
    help = "Send in-app (and email) reminders for sessions starting soon. Run every 15–60 minutes from cron."

    def handle(self, *args, **options):
        count = send_reminders()
        self.stdout.write(self.style.SUCCESS(f"Sent reminders for {count} session(s)."))
