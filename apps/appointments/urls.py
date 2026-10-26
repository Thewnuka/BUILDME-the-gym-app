from django.urls import path

from . import views

app_name = "appointments"

urlpatterns = [
    path("", views.AppointmentListView.as_view(), name="list"),
    path("book/", views.StaffBookView.as_view(), name="book"),
    path("<int:pk>/", views.AppointmentDetailView.as_view(), name="detail"),
    path("<int:pk>/reschedule/", views.RescheduleView.as_view(), name="reschedule"),
    path("<int:pk>/cancel/", views.CancelView.as_view(), name="cancel"),
    path("<int:pk>/outcome/", views.CompleteView.as_view(), name="outcome"),
    path("session-types/", views.SessionTypeListView.as_view(), name="session_types"),
    path("session-types/new/", views.SessionTypeCreateView.as_view(), name="session_type_create"),
    path("session-types/<int:pk>/edit/", views.SessionTypeUpdateView.as_view(), name="session_type_update"),
    path("my/", views.MyAppointmentsView.as_view(), name="my_list"),
    path("my/book/", views.MyBookView.as_view(), name="my_book"),
    path("my/<int:pk>/", views.MyAppointmentDetailView.as_view(), name="my_detail"),
    path("my/<int:pk>/reschedule/", views.MyRescheduleView.as_view(), name="my_reschedule"),
    path("my/<int:pk>/cancel/", views.MyCancelView.as_view(), name="my_cancel"),
]
