import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from .models import WorkReport
from employees.models import Department, Employee
from employees.sop_sync import import_sop_workers, operation_codes_for_job_title


@override_settings(INTERNAL_REPORT_API_KEY="test-api-key")
class ReceiveWorkReportTests(TestCase):
    payload = {
        "sourceReportId": "legacy-42", "idempotencyKey": "request-42", "productionId": "1001", "workorderId": "2001",
        "workerId": "EMP-1", "workerName": "张三", "operation": "assembly", "operationLabel": "组装", "qty": 2,
        "date": "2026-08-12", "time": "10:30", "materials": [{"productId": 1, "bomLineId": 2, "defaultCode": "MAT-1", "actualQty": 1.5, "uomId": 1}],
    }

    def post(self, payload=None):
        return self.client.post("/internal/api/v1/work-reports/", data=json.dumps(payload or self.payload), content_type="application/json", headers={"X-Internal-API-Key": "test-api-key"})

    def test_internal_api_uses_api_key_without_csrf_cookie(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)
        response = csrf_client.post(
            "/internal/api/v1/work-reports/",
            data=json.dumps(self.payload),
            content_type="application/json",
            headers={"X-Internal-API-Key": "test-api-key"},
        )
        self.assertEqual(response.status_code, 201)

        status_response = csrf_client.post(
            "/internal/api/v1/work-reports/sync-status/",
            data=json.dumps({
                "sourceReportId": "legacy-42",
                "idempotencyKey": "request-42",
                "eventKey": "legacy-42-csrf-final",
                "syncStatus": "synced",
            }),
            content_type="application/json",
            headers={"X-Internal-API-Key": "test-api-key"},
        )
        self.assertEqual(status_response.status_code, 200)

    def test_csrf_exemption_does_not_bypass_api_key(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)
        response = csrf_client.post(
            "/internal/api/v1/work-reports/",
            data=json.dumps(self.payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_creates_report_and_snapshot(self):
        response = self.post()
        self.assertEqual(response.status_code, 201)
        report = WorkReport.objects.get()
        self.assertEqual(report.material_snapshots.count(), 1)
        self.assertEqual(report.sync_events.count(), 1)

    def test_replay_is_idempotent(self):
        self.post()
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WorkReport.objects.count(), 1)

    def test_rejects_conflicting_idempotency_key(self):
        self.post()
        payload = {**self.payload, "sourceReportId": "legacy-43"}
        response = self.post(payload)
        self.assertEqual(response.status_code, 409)

    def test_rejects_missing_api_key(self):
        response = self.client.post("/internal/api/v1/work-reports/", data=json.dumps(self.payload), content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_records_sync_status_once(self):
        self.post()
        payload = {"sourceReportId": "legacy-42", "idempotencyKey": "request-42", "eventKey": "legacy-42-final", "syncStatus": "synced", "materialSyncStatus": "synced"}
        response = self.client.post("/internal/api/v1/work-reports/sync-status/", data=json.dumps(payload), content_type="application/json", headers={"X-Internal-API-Key": "test-api-key"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], True)
        response = self.client.post("/internal/api/v1/work-reports/sync-status/", data=json.dumps(payload), content_type="application/json", headers={"X-Internal-API-Key": "test-api-key"})
        self.assertEqual(response.json()["created"], False)
        self.assertEqual(WorkReport.objects.get().sync_status, "synced")


class EmployeeAdministrationTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser("admin", "admin@example.com", "password")
        self.client.force_login(self.admin_user)

    def create_employee(self, **data):
        payload = {
            "name": "张三", "email": "zhangsan@example.com", "department_name": "生产部门",
            "job_title": "组装", "phone": "13800138000",
        }
        payload.update(data)
        return self.client.post(reverse("admin:employees_employee_add"), payload)

    def test_creating_employee_creates_department_and_persists(self):
        response = self.create_employee()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Department.objects.filter(name="生产部门").count(), 1)
        self.assertEqual(Employee.objects.get().department.name, "生产部门")

    def test_existing_department_is_reused(self):
        department = Department.objects.create(name="生产部门")
        response = self.create_employee()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Department.objects.get(name="生产部门").pk, department.pk)

    def test_invalid_phone_is_rejected(self):
        response = self.create_employee(phone="invalid")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Employee.objects.count(), 0)

    @patch("employees.admin.enqueue_sop_employee_sync")
    def test_new_employee_receives_a_stable_sop_worker_id(self, sync_employee):
        response = self.create_employee(job_title="组装，打包")
        self.assertEqual(response.status_code, 302)
        employee = Employee.objects.get()
        self.assertEqual(employee.source_worker_id, f"ADMIN_EMP_{employee.pk}")
        sync_employee.assert_called_once_with(employee.pk)


class SopEmployeeImportTests(TestCase):
    @patch("employees.sop_sync.fetch_sop_workers")
    def test_imports_worker_department_and_job_operations(self, fetch_workers):
        fetch_workers.return_value = [{
            "id": "LOCAL_LWH", "name": "罗伟华", "team": "组装班",
            "operationCodes": ["pc_assembly_tape", "pc_assembly_splitter"],
        }]
        self.assertEqual(import_sop_workers(), {"fetched": 1, "created": 1, "updated": 0, "skipped": 0})
        employee = Employee.objects.get()
        self.assertEqual(employee.department.name, "组装部")
        self.assertEqual(employee.job_title, "组装，打包")
        self.assertEqual(employee.operation_codes, ["pc_assembly_tape", "pc_assembly_splitter"])
