from django.urls import path

from . import views

app_name = "equipment"

urlpatterns = [
    path("", views.EquipmentListView.as_view(), name="list"),
    path("new/", views.EquipmentCreateView.as_view(), name="create"),
    path("<int:pk>/", views.EquipmentDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.EquipmentUpdateView.as_view(), name="update"),
    path("<int:pk>/fault/", views.ReportFaultView.as_view(), name="report_fault"),
    path("<int:pk>/maintenance/", views.RecordMaintenanceView.as_view(), name="record_maintenance"),
    path("<int:pk>/status/", views.SetStatusView.as_view(), name="set_status"),
]
