import json

from django.http import FileResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from reports.views import _api_authorized

from .models import Employee, EmployeeReportPanelAccount, ProcessSOP
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


@require_GET
def internal_process_sop_list(request):
    if not _api_authorized(request):
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    process_code = str(request.GET.get("processCode", "")).strip()
    if not process_code:
        return JsonResponse({"ok": False, "error": "缺少 processCode", "data": []}, status=400)
    rows = ProcessSOP.objects.filter(process__code=process_code, is_active=True).select_related("process").order_by("-created_at", "-id")
    data = [{
        "id": row.pk,
        "name": row.title,
        "title": row.title,
        "version": row.version,
        "fileType": "application/pdf",
        "sopUrl": f"/internal/api/v1/process-sops/{row.pk}/download/",
        "processCode": row.process.code,
        "processName": row.process.name,
    } for row in rows]
    return JsonResponse({"ok": True, "data": data, "meta": {"count": len(data), "source": "report_admin"}})


@require_GET
def internal_process_sop_download(request, sop_id):
    if not _api_authorized(request):
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    sop = ProcessSOP.objects.filter(pk=sop_id, is_active=True).first()
    if not sop or not sop.pdf_file:
        return JsonResponse({"detail": "SOP not found"}, status=404)
    response = FileResponse(sop.pdf_file.open("rb"), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="SOP-{sop.pk}.pdf"'
    return response
