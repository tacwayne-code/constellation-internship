import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from reports.views import _api_authorized

from .models import Employee, EmployeeReportPanelAccount
from .sop_sync import employee_payload


@require_GET
def internal_employee_list(request):
    if not _api_authorized(request):
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    employees = Employee.objects.select_related("department").order_by("department__name", "name", "id")
    return JsonResponse({"data": [employee_payload(employee) for employee in employees]})


@csrf_exempt
@require_POST
def internal_employee_panel_auth(request):
    """Verify a panel account for the SOP service without exposing its hash."""
    if not _api_authorized(request):
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "Invalid payload"}, status=400)

    username = str(payload.get("username", "")).strip()
    password = payload.get("password")
    if not username or not isinstance(password, str):
        return JsonResponse({"detail": "Invalid credentials"}, status=401)

    account = (
        EmployeeReportPanelAccount.objects.select_related("employee__department")
        .filter(username=username, is_active=True)
        .first()
    )
    if account is None or not account.check_password(password):
        return JsonResponse({"detail": "Invalid credentials"}, status=401)
    return JsonResponse({"data": employee_payload(account.employee)})
