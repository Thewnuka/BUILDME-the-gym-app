from django.urls import path

from . import views

app_name = "trainers"

urlpatterns = [
    path("", views.TrainerListView.as_view(), name="list"),
    path("new/", views.TrainerCreateView.as_view(), name="create"),
    path("me/", views.MyTrainerProfileView.as_view(), name="me"),
    path("<int:pk>/", views.TrainerDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.TrainerUpdateView.as_view(), name="update"),
    path("<int:pk>/photo/", views.TrainerPhotoView.as_view(), name="photo"),
    path("<int:pk>/availability/", views.AvailabilityAddView.as_view(), name="availability_add"),
    path("<int:pk>/availability/<int:slot_pk>/delete/", views.AvailabilityDeleteView.as_view(), name="availability_delete"),
    path("<int:pk>/time-off/", views.TimeOffAddView.as_view(), name="time_off_add"),
    path("<int:pk>/time-off/<int:item_pk>/delete/", views.TimeOffDeleteView.as_view(), name="time_off_delete"),
    path("<int:pk>/certificates/", views.CertificateAddView.as_view(), name="certificate_add"),
    path("<int:pk>/certificates/<int:cert_pk>/", views.CertificateFileView.as_view(), name="certificate_file"),
    path("<int:pk>/certificates/<int:cert_pk>/delete/", views.CertificateDeleteView.as_view(), name="certificate_delete"),
    path("<int:pk>/assign/", views.AssignMemberView.as_view(), name="assign_member"),
    path("<int:pk>/pay/", views.PayRecordAddView.as_view(), name="pay_add"),
    path("assign-to-member/<int:member_pk>/", views.AssignTrainerToMemberView.as_view(), name="assign_to_member"),
    path("assignments/<int:assignment_pk>/end/", views.EndAssignmentView.as_view(), name="end_assignment"),
]
