import os
import sys
import threading

from django.apps import AppConfig


class ReportsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'reports'
    verbose_name = '生产报工'

    def ready(self):
        # Local runserver uses --noreload, so one daemon poller is sufficient.
        # Production deployments can schedule the sync_sop_reports command.
        if "runserver" not in sys.argv or os.environ.get("RUN_MAIN") == "true":
            return
        from .sop_sync import run_sync_loop

        threading.Thread(
            target=run_sync_loop,
            name="sop-report-sync",
            daemon=True,
        ).start()
