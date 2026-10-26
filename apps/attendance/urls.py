from django.urls import path

from . import views

app_name = "attendance"

urlpatterns = [
    path("", views.AttendanceListView.as_view(), name="list"),
    path("check-in/", views.CheckInDeskView.as_view(), name="desk"),
    path("check-in/manual/", views.ManualCheckInView.as_view(), name="check_in"),
    path("check-in/qr/", views.QRCheckInView.as_view(), name="check_in_qr"),
    path("<int:pk>/check-out/", views.CheckOutView.as_view(), name="check_out"),
    path("daily/", views.DailyReportView.as_view(), name="daily"),
    path("pass/<int:member_pk>/", views.MemberPassView.as_view(), name="pass"),
    path("pass/<int:member_pk>/reissue/", views.ReissuePassView.as_view(), name="reissue_pass"),
]
