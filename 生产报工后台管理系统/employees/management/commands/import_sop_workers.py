import json

from django.core.management.base import BaseCommand, CommandError

from employees.sop_sync import SopEmployeeSyncError, import_sop_workers


class Command(BaseCommand):
    help = "Read the current SOP worker list through HTTP and import it into the management system."

    def handle(self, *args, **options):
        try:
            summary = import_sop_workers()
        except SopEmployeeSyncError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(json.dumps(summary, ensure_ascii=False)))
