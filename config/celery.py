"""
Optional Celery setup. The app does NOT need Celery: by default schedule
``python manage.py run_scheduled_tasks`` with cron or Windows Task Scheduler.

To use Celery + Redis instead:
    pip install "celery[redis]"
    set CELERY_BROKER_URL=redis://localhost:6379/0 in .env
    celery -A config worker -l info          # worker
    celery -A config beat -l info            # scheduler (every 15 minutes)
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("buildme")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["apps.core"])
app.conf.beat_schedule = {
    "run-scheduled-tasks": {"task": "core.run_scheduled_tasks", "schedule": 15 * 60},
}
