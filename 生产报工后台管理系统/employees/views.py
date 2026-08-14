from django.http import JsonResponse
from django.views.decorators.http import require_GET

from reports.views import _api_authorized

from .models import Employee
from .sop_sync import employee_payload


@require_GET
def internal_employee_list(request):
    if not _api_authorized(request):
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    employees = Employee.objects.select_related("department").order_by("department__name", "name", "id")
    return JsonResponse({"data": [employee_payload(employee) for employee in employees]})
