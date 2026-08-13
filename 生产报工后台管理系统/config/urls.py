from django.contrib import admin
from django.shortcuts import redirect
from django.urls import path

from reports import views

urlpatterns = [
    path("", lambda request: redirect("admin/"), name="home"),
    path("admin/", admin.site.urls),
    path("reports/statistics/", views.statistics, name="report-statistics"),
    path("reports/export.csv", views.export_csv, name="report-export-csv"),
    path("internal/api/v1/work-reports/", views.receive_work_report, name="receive-work-report"),
    path("internal/api/v1/work-reports/sync-status/", views.receive_sync_status, name="receive-sync-status"),
]
