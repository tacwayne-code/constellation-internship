import json

from django.core.management.base import BaseCommand

from employees.models import Employee
from employees.sop_sync import push_employee_to_sop


class Command(BaseCommand):
    help = "Push all management-system employees to the SOP worker mirror through HTTP."

    def handle(self, *args, **options):
        summary = {"pushed": 0, "failed": 0}
        for employee in Employee.objects.select_related("department").order_by("id"):
            if push_employee_to_sop(employee):
                summary["pushed"] += 1
            else:
                summary["failed"] += 1
        self.stdout.write(self.style.SUCCESS(json.dumps(summary, ensure_ascii=False)))
