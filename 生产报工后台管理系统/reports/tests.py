import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.contrib.auth.hashers import check_password
from django.contrib.auth import get_user_model
from django.urls import reverse

from .models import WorkReport
from employees.models import (
    Department,
    Employee,
    EmployeeReportPanelAccount,
    EmployeeProcessAuthorization,
    JobPosition,
    WorkProcess,
)
from employees.sop_sync import (
    department_name_from_sop_team,
    employee_payload,
    import_sop_workers,
    operation_codes_for_job_title,
)
from employees.templatetags.employee_menu import department_menu_items


@override_settings(INTERNAL_REPORT_API_KEY="test-api-key")
class ReceiveWorkReportTests(TestCase):
    payload = {
        "sourceReportId": "legacy-42", "idempotencyKey": "request-42", "productionId": "1001", "workorderId": "2001",
        "workerId": "EMP-1", "workerName": "张三", "operation": "assembly", "operationLabel": "组装", "qty": 2,
        "productionName": "MO-01001", "orderProduct": "Test Product",
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

    @patch("reports.views.get_cached_production_details")
    def test_uses_cached_production_name_and_product(self, get_cached_production_details):
        get_cached_production_details.return_value = {
            "1001": {"production_name": "MO-01001", "product_name": "Test Product"},
        }
        payload = {key: value for key, value in self.payload.items() if key not in {"productionName", "orderProduct"}}

        response = self.post(payload)

        self.assertEqual(response.status_code, 201)
        report = WorkReport.objects.get()
        self.assertEqual(report.production_name, "MO-01001")
        self.assertEqual(report.order_product, "Test Product")

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

    def test_creating_panel_account_hashes_the_password_and_links_the_employee(self):
        department = Department.objects.create(name="生产车间")
        employee = Employee.objects.create(name="张三", department=department, job_title="组装")

        response = self.client.post(
            reverse("admin:employees_employeereportpanelaccount_add"),
            {
                "employee": employee.pk,
                "username": "zhangsan",
                "password": "SopPanel123!",
                "password_confirmation": "SopPanel123!",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        account = EmployeeReportPanelAccount.objects.get()
        self.assertEqual(account.employee, employee)
        self.assertTrue(check_password("SopPanel123!", account.password_hash))

    def test_creating_panel_account_accepts_a_short_password(self):
        department = Department.objects.create(name="生产车间")
        employee = Employee.objects.create(name="李四", department=department, job_title="组装")

        response = self.client.post(
            reverse("admin:employees_employeereportpanelaccount_add"),
            {
                "employee": employee.pk,
                "username": "lisi",
                "password": "密",
                "password_confirmation": "密",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(check_password("密", EmployeeReportPanelAccount.objects.get(username="lisi").password_hash))

    @override_settings(INTERNAL_REPORT_API_KEY="test-api-key")
    def test_panel_account_auth_returns_only_the_authenticated_employee(self):
        department = Department.objects.create(name="生产车间")
        employee = Employee.objects.create(
            name="周小明",
            department=department,
            job_title="组装，打包",
            source_worker_id="ADMIN_EMP_8",
            operation_codes=["worker_assembly", "worker_packing"],
        )
        account = EmployeeReportPanelAccount(employee=employee, username="zhou")
        account.set_password("密")
        account.save()

        response = self.client.post(
            reverse("internal-employee-panel-auth"),
            data=json.dumps({"username": "zhou", "password": "密"}),
            content_type="application/json",
            headers={"X-Internal-API-Key": "test-api-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {
            "sourceWorkerId": "ADMIN_EMP_8",
            "name": "周小明",
            "team": "生产车间",
            "departmentName": "生产车间",
            "jobTitle": "组装，打包",
            "operationCodes": ["worker_assembly", "worker_packing"],
            "source": "report_admin",
        })

    @override_settings(INTERNAL_REPORT_API_KEY="test-api-key")
    def test_panel_account_auth_rejects_invalid_credentials(self):
        response = self.client.post(
            reverse("internal-employee-panel-auth"),
            data=json.dumps({"username": "missing", "password": "anything"}),
            content_type="application/json",
            headers={"X-Internal-API-Key": "test-api-key"},
        )

        self.assertEqual(response.status_code, 401)

    def test_custom_assembly_job_payload_contains_named_bom_binding(self):
        department = Department.objects.create(name="生产车间")
        employee = Employee.objects.create(
            name="周小明",
            department=department,
            job_title="定位结构组装，打包",
            source_worker_id="ADMIN_EMP_9",
            operation_codes=operation_codes_for_job_title("定位结构组装，打包"),
        )
        payload = employee_payload(employee)
        self.assertIn("operationBindings", payload)
        binding = next(item for item in payload["operationBindings"] if item["name"] == "定位结构组装")
        self.assertEqual(binding["workorderNames"], ["定位结构组装"])
        self.assertTrue(binding["requiresBom"])

    def test_panel_payload_merges_job_title_operations_with_legacy_codes(self):
        department = Department.objects.create(name="生产车间")
        employee = Employee.objects.create(
            name="周小明",
            department=department,
            job_title="定位结构组装，打包",
            source_worker_id="ADMIN_EMP_10",
            operation_codes=["worker_packing", "legacy_operation"],
        )
        payload = employee_payload(employee)
        self.assertEqual(payload["operationCodes"], [
            "worker_assembly_custom_0f0cb3b8592d0eef",
            "worker_packing",
            "legacy_operation",
        ])

    def test_payload_exposes_only_active_two_level_process_grants(self):
        department = Department.objects.create(name="生产车间")
        employee = Employee.objects.create(name="张三", department=department, job_title="组装")
        assembly = JobPosition.objects.create(code="assembly", name="组装")
        packing = JobPosition.objects.create(code="packing", name="打包")
        locating = WorkProcess.objects.create(
            position=assembly, code="locating-assembly", name="定位结构组装",
            wo_match_rules={"routingOperationIds": [101], "workcenterIds": [5]},
        )
        disabled = WorkProcess.objects.create(position=packing, code="packing-a", name="打包", is_active=False)
        EmployeeProcessAuthorization.objects.create(employee=employee, position=assembly, process=locating)
        EmployeeProcessAuthorization.objects.create(employee=employee, position=packing, process=disabled)

        self.assertEqual(employee_payload(employee)["jobRoles"], [{
            "code": "assembly", "name": "组装", "enabled": True,
            "operations": [{
                "code": "locating-assembly", "name": "定位结构组装", "enabled": True,
                "woMatch": {"routingOperationIds": [101], "workcenterIds": [5]},
            }],
        }])

    def test_process_authorization_rejects_process_from_another_position(self):
        department = Department.objects.create(name="生产车间")
        employee = Employee.objects.create(name="张三", department=department, job_title="组装")
        assembly = JobPosition.objects.create(code="assembly", name="组装")
        packing = JobPosition.objects.create(code="packing", name="打包")
        process = WorkProcess.objects.create(position=assembly, code="locating-assembly", name="定位结构组装")
        grant = EmployeeProcessAuthorization(employee=employee, position=packing, process=process)
        with self.assertRaises(Exception):
            grant.full_clean()

    @patch("employees.admin.enqueue_sop_employee_sync")
    def test_authorization_status_change_updates_its_process_and_all_grants(self, sync_employee):
        department = Department.objects.create(name="生产车间")
        first = Employee.objects.create(name="张三", department=department, job_title="组装")
        second = Employee.objects.create(name="李四", department=department, job_title="组装")
        position = JobPosition.objects.create(code="assembly", name="组装")
        process = WorkProcess.objects.create(position=position, code="assembly-a", name="结构组装")
        grant = EmployeeProcessAuthorization.objects.create(employee=first, position=position, process=process)
        other_grant = EmployeeProcessAuthorization.objects.create(employee=second, position=position, process=process)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:employees_employeeprocessauthorization_change", args=[grant.pk]),
                {"employee": first.pk, "position": position.pk, "process": process.pk},
            )

        self.assertEqual(response.status_code, 302)
        process.refresh_from_db()
        other_grant.refresh_from_db()
        self.assertFalse(process.is_active)
        self.assertFalse(other_grant.is_active)
        self.assertEqual({call.args[0] for call in sync_employee.call_args_list}, {first.pk, second.pk})

    @patch("employees.admin.enqueue_sop_employee_sync")
    def test_selected_authorizations_can_be_deleted(self, sync_employee):
        department = Department.objects.create(name="生产车间")
        employee = Employee.objects.create(name="张三", department=department, job_title="组装")
        position = JobPosition.objects.create(code="assembly", name="组装")
        process = WorkProcess.objects.create(position=position, code="assembly-a", name="结构组装")
        grant = EmployeeProcessAuthorization.objects.create(employee=employee, position=position, process=process)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:employees_employeeprocessauthorization_delete_selected"),
                {"authorization_ids": [grant.pk]},
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(EmployeeProcessAuthorization.objects.filter(pk=grant.pk).exists())
        self.assertFalse(WorkProcess.objects.filter(pk=process.pk).exists())
        sync_employee.assert_called_once_with(employee.pk)

    @patch("employees.admin.enqueue_sop_employee_sync")
    def test_selected_processes_delete_related_authorizations(self, sync_employee):
        department = Department.objects.create(name="生产车间")
        employee = Employee.objects.create(name="张三", department=department, job_title="组装")
        position = JobPosition.objects.create(code="assembly", name="组装")
        process = WorkProcess.objects.create(position=position, code="assembly-a", name="结构组装")
        EmployeeProcessAuthorization.objects.create(employee=employee, position=position, process=process)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:employees_employeeprocessauthorization_process_delete"),
                {"process_ids": [process.pk]},
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(WorkProcess.objects.filter(pk=process.pk).exists())
        self.assertEqual(EmployeeProcessAuthorization.objects.count(), 0)
        sync_employee.assert_called_once_with(employee.pk)

    @patch("employees.admin.enqueue_sop_employee_sync")
    def test_selected_positions_delete_related_processes_and_authorizations(self, sync_employee):
        department = Department.objects.create(name="生产车间")
        employee = Employee.objects.create(name="张三", department=department, job_title="组装")
        position = JobPosition.objects.create(code="assembly", name="组装")
        process = WorkProcess.objects.create(position=position, code="assembly-a", name="结构组装")
        EmployeeProcessAuthorization.objects.create(employee=employee, position=position, process=process)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:employees_jobposition_delete_selected"),
                {"position_ids": [position.pk]},
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(JobPosition.objects.filter(pk=position.pk).exists())
        self.assertFalse(WorkProcess.objects.filter(pk=process.pk).exists())
        self.assertEqual(EmployeeProcessAuthorization.objects.count(), 0)
        sync_employee.assert_called_once_with(employee.pk)

    def test_assembly_department_normalizes_legacy_and_generic_codes_to_host_routes(self):
        department = Department.objects.create(name="组装部")
        employee = Employee.objects.create(
            name="罗伟华",
            department=department,
            job_title="组装，打包",
            source_worker_id="ADMIN_EMP_11",
            operation_codes=[
                "worker_assembly", "worker_packing",
                "pc_assembly_tape", "pc_assembly_splitter",
            ],
        )
        self.assertEqual(employee_payload(employee)["operationCodes"], [
            "pc_assembly_tape",
            "pc_assembly_splitter",
        ])

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

    @patch("employees.sop_sync.fetch_sop_workers")
    def test_reuses_the_legacy_odoo_employee_when_sop_changes_to_admin_id(self, fetch_workers):
        department = Department.objects.create(name="生产车间")
        employee = Employee.objects.create(
            source_worker_id="ODOO_EMP_19",
            name="周小明",
            department=department,
            job_title="组装，打包",
            operation_codes=["worker_assembly", "worker_packing"],
        )
        fetch_workers.return_value = [{
            "id": "ADMIN_EMP_1", "name": "周小明", "team": "生产车间",
            "jobTitle": "组装，打包",
        }]
        self.assertEqual(import_sop_workers(), {"fetched": 1, "created": 0, "updated": 1, "skipped": 0})
        employee.refresh_from_db()
        self.assertEqual(employee.source_worker_id, "ADMIN_EMP_1")
        self.assertEqual(Employee.objects.count(), 1)


class EmployeeMenuTests(TestCase):
    def test_department_menu_items_link_to_department_filtered_employees(self):
        department = Department.objects.create(name="组装部")
        items = json.loads(str(department_menu_items()))
        self.assertEqual(items[0]["name"], "组装部")
        self.assertEqual(items[0]["url"], f"/admin/employees/employee/?department__id__exact={department.pk}")

    def test_workshop_name_remains_the_employee_department(self):
        self.assertEqual(department_name_from_sop_team("生产车间", "组装，打包"), "生产车间")
