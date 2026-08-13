from django.urls import path

from . import views

urlpatterns = [
    path("statistics/", views.statistics),
    path("export.csv", views.export_csv),
]
