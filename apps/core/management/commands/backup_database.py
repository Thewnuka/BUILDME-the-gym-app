"""Back up the database (and optionally uploaded files) into a timestamped file.

SQLite: uses SQLite's online backup API, so it is safe while the app is running.
PostgreSQL: runs ``pg_dump`` (must be installed and on PATH) in custom format;
restore with ``pg_restore --clean --dbname=... <file>``.
"""
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone


class Command(BaseCommand):
    help = "Back up the database (and optionally media files) to a timestamped file."

    def add_arguments(self, parser):
        parser.add_argument("--output", default=str(settings.BASE_DIR / "backups"),
                            help="Directory for the backup files (default: ./backups).")
        parser.add_argument("--media", action="store_true", help="Also archive uploaded files (MEDIA_ROOT) as a .zip.")

    def handle(self, *args, **options):
        out = Path(options["output"])
        out.mkdir(parents=True, exist_ok=True)
        stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        db = settings.DATABASES["default"]
        if connection.vendor == "sqlite":
            target = out / f"db-{stamp}.sqlite3"
            source = sqlite3.connect(str(db["NAME"]))
            dest = sqlite3.connect(str(target))
            try:
                with dest:
                    source.backup(dest)
            finally:
                dest.close()
                source.close()
        elif connection.vendor == "postgresql":
            if not shutil.which("pg_dump"):
                raise CommandError("pg_dump was not found on PATH. Install the PostgreSQL client tools.")
            target = out / f"db-{stamp}.dump"
            env = {**os.environ, "PGPASSWORD": str(db.get("PASSWORD") or "")}
            cmd = ["pg_dump", "--format=custom", "--no-owner", f"--file={target}",
                   f"--host={db.get('HOST') or 'localhost'}", f"--port={db.get('PORT') or 5432}",
                   f"--username={db.get('USER')}", db["NAME"]]
            result = subprocess.run(cmd, env=env, capture_output=True, text=True)
            if result.returncode != 0:
                raise CommandError(f"pg_dump failed: {result.stderr.strip()[:500]}")
        else:
            raise CommandError(f"Backups for the {connection.vendor} database engine are not supported.")
        self.stdout.write(self.style.SUCCESS(f"Database backup: {target} ({target.stat().st_size // 1024} KB)"))

        if options["media"]:
            media = Path(settings.MEDIA_ROOT)
            if media.exists():
                archive = shutil.make_archive(str(out / f"media-{stamp}"), "zip", root_dir=media)
                self.stdout.write(self.style.SUCCESS(f"Media backup: {archive}"))
            else:
                self.stdout.write("No media folder yet; nothing to archive.")
