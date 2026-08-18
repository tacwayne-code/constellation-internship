import json
import hashlib
import logging
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction

from .models import Department, Employee


logger = logging.getLogger(__name__)

# These are the stable SOP operation codes. Job titles use their displayed names.
OPERATION_CODES_BY_NAME = {
    "总装": ("assembly",),
    "测试": ("testing",),
    "质检": ("qc",),
    "包装": ("packing",),
    "调试": ("debug",),
    "组装": ("worker_assembly",),
    "电控": ("worker_electrical",),
    "打包": ("worker_packing",),
}
OPERATION_NAMES_BY_CODE = {
    code: name
    for name, codes in OPERATION_CODES_BY_NAME.items()
    for code in codes
}
OPERATION_NAMES_BY_CODE.update({
    "pc_assembly_tape": "组装",
    "pc_assembly_splitter": "打包",
})


def operation_bindings_for_job_title(value):
    """Return stable, descriptive SOP bindings for every assigned job name.

    The management system previously had codes only for the generic jobs.  A
    BOM-routed assembly job is named after its component (for example,
    ``定位结构组装``), so it needs its own stable code and work-order match.
    """
    bindings = []
    for name in split_job_title(value):
        static_codes = OPERATION_CODES_BY_NAME.get(name, ())
        if static_codes:
            for code in static_codes:
                bindings.append({
                    "code": code,
                    "name": name,
                    "workorderNames": [name],
                    "productClass": "machine" if code.startswith("worker_") else None,
                    "requiresBom": False,
                })
            continue
        if name.endswith("组装"):
            digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
            bindings.append({
                "code": f"worker_assembly_custom_{digest}",
                "name": name,
                "workorderNames": [name],
                "productClass": "machine",
                "requiresBom": True,
            })
    return bindings


class SopEmployeeSyncError(RuntimeError):
    pass


def split_job_title(value):
    return [part.strip() for part in re.split(r"[、,，/;；]+", str(value or "")) if part.strip()]


def operation_codes_for_job_title(value):
    codes = []
    for binding in operation_bindings_for_job_title(value):
        if binding["code"] not in codes:
            codes.append(binding["code"])
    return codes


def _job_title_from_sop_worker(worker):
    names = worker.get("jobOperationNames")
    if not isinstance(names, list):
        names = split_job_title(worker.get("jobTitle", ""))
    if not names:
        names = [OPERATION_NAMES_BY_CODE[code] for code in worker.get("operationCodes", []) if code in OPERATION_NAMES_BY_CODE]
    unique_names = []
    for name in names:
        text = str(name).strip()
        if text and text not in unique_names:
            unique_names.append(text)
    return "，".join(unique_names)


def _operation_codes_from_sop_worker(worker, job_title):
    incoming = worker.get("operationCodes", [])
    if str(worker.get("id", "")) == "LOCAL_LWH" and isinstance(incoming, list):
        # Keep the existing host-specific assembly/BOM workflow for this local worker.
        allowed = {"pc_assembly_tape", "pc_assembly_splitter"}
        codes = [str(code) for code in incoming if str(code) in allowed]
        if codes:
            return codes
    return operation_codes_for_job_title(job_title)


def department_name_from_sop_team(team, job_title):
    name = str(team or "").strip()
    if name.endswith("班"):
        return f"{name[:-1]}部"
    if name:
        return name
    operations = split_job_title(job_title)
    return f"{operations[0]}部" if operations else "未分配部门"


def employee_source_worker_id(employee):
    return employee.source_worker_id or f"ADMIN_EMP_{employee.pk}"


def employee_payload(employee):
    bindings = operation_bindings_for_job_title(employee.job_title)
    payload = {
        "sourceWorkerId": employee_source_worker_id(employee),
        "name": employee.name,
        "team": employee.department.name,
        "departmentName": employee.department.name,
        "jobTitle": employee.job_title,
        "operationCodes": employee.operation_codes or [binding["code"] for binding in bindings],
        "source": "report_admin",
    }
    static_codes = {code for codes in OPERATION_CODES_BY_NAME.values() for code in codes}
    custom_bindings = [binding for binding in bindings if binding["code"] not in static_codes]
    if custom_bindings:
        payload["operationBindings"] = custom_bindings
    return payload


def fetch_sop_workers():
    request = Request(settings.SOP_WORKERS_API_URL, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise SopEmployeeSyncError(f"Unable to read SOP workers: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("data"), list):
        raise SopEmployeeSyncError("SOP workers API returned an invalid response")
    return payload["data"]


def import_sop_workers():
    summary = {"fetched": 0, "created": 0, "updated": 0, "skipped": 0}
    workers = fetch_sop_workers()
    summary["fetched"] = len(workers)
    for worker in workers:
        source_worker_id = str(worker.get("id", "")).strip()
        name = str(worker.get("name", "")).strip()
        job_title = _job_title_from_sop_worker(worker)
        operation_codes = _operation_codes_from_sop_worker(worker, job_title)
        if not source_worker_id or not name or not operation_codes:
            summary["skipped"] += 1
            continue
        department_name = department_name_from_sop_team(worker.get("team", ""), job_title)
        with transaction.atomic():
            department, _ = Department.objects.get_or_create(name=department_name)
            employee = Employee.objects.filter(source_worker_id=source_worker_id).first()
            created = False
            if employee is None and source_worker_id.startswith("ADMIN_EMP_"):
                # One-time transition from the former Odoo-derived SOP IDs to
                # the stable management-system IDs, without duplicating staff.
                legacy_matches = Employee.objects.filter(
                    source_worker_id__startswith="ODOO_EMP_",
                    name=name,
                )
                if legacy_matches.count() == 1:
                    employee = legacy_matches.first()
                    employee.source_worker_id = source_worker_id
            if employee is None:
                employee = Employee(
                    source_worker_id=source_worker_id,
                    name=name,
                    department=department,
                    job_title=job_title,
                    operation_codes=operation_codes,
                )
                created = True
            if created:
                employee.save()
                summary["created"] += 1
            else:
                changed = False
                for field, value in (("source_worker_id", source_worker_id), ("name", name), ("department", department), ("job_title", job_title), ("operation_codes", operation_codes)):
                    if getattr(employee, field) != value:
                        setattr(employee, field, value)
                        changed = True
                if changed:
                    employee.save(update_fields=("source_worker_id", "name", "department", "job_title", "operation_codes", "updated_at"))
                    summary["updated"] += 1
    return summary


def push_employee_to_sop(employee):
    body = json.dumps(employee_payload(employee), ensure_ascii=False).encode("utf-8")
    request = Request(
        settings.SOP_EMPLOYEE_SYNC_URL,
        data=body,
        headers={"Content-Type": "application/json", "X-Internal-API-Key": settings.INTERNAL_REPORT_API_KEY},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Unable to push employee %s to SOP: %s", employee.pk, exc)
        return False
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        logger.warning("SOP rejected employee %s sync", employee.pk)
        return False
    return True


def enqueue_sop_employee_sync(employee_id):
    def sync():
        try:
            employee = Employee.objects.select_related("department").get(pk=employee_id)
            push_employee_to_sop(employee)
        except Employee.DoesNotExist:
            return
        except Exception:
            logger.exception("Unexpected employee SOP sync failure for %s", employee_id)

    threading.Thread(target=sync, name=f"sop-employee-sync-{employee_id}", daemon=True).start()
