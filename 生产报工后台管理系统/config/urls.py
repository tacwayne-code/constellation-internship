from django.contrib import admin
from django.shortcuts import redirect
from django.urls import path

from employees.views import internal_employee_list, internal_employee_panel_auth
from reports import views

urlpatterns = [
    path("", lambda request: redirect("admin/"), name="home"),
    path("admin/", admin.site.urls),
    path("reports/statistics/", views.statistics, name="report-statistics"),
    path("reports/export.csv", views.export_csv, name="report-export-csv"),
    path("internal/api/v1/work-reports/", views.receive_work_report, name="receive-work-report"),
    path("internal/api/v1/work-reports/sync-status/", views.receive_sync_status, name="receive-sync-status"),
    path("internal/api/v1/employees/", internal_employee_list, name="internal-employee-list"),
    path("internal/api/v1/employee-panel-auth/", internal_employee_panel_auth, name="internal-employee-panel-auth"),
]
