import json

from django.core.management.base import BaseCommand, CommandError

from reports.sop_sync import SopSyncError, sync_sop_reports


class Command(BaseCommand):
    help = "Read work reports from the configured SOP HTTP API and sync them to MySQL."

    def handle(self, *args, **options):
        try:
            summary = sync_sop_reports()
        except SopSyncError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(json.dumps(summary, ensure_ascii=False)))
