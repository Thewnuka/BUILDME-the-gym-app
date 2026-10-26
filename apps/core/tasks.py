"""Optional Celery task wrapper. Only imported when Celery is installed (see config/celery.py)."""
from celery import shared_task

from .scheduled import run_all


@shared_task(name="core.run_scheduled_tasks")
def run_scheduled_tasks():
    return run_all()
