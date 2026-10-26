from django.urls import path

from . import views

app_name = "announcements"

urlpatterns = [
    path("", views.AnnouncementBoardView.as_view(), name="board"),
    path("manage/", views.AnnouncementAdminListView.as_view(), name="manage"),
    path("manage/new/", views.AnnouncementCreateView.as_view(), name="create"),
    path("manage/<int:pk>/edit/", views.AnnouncementUpdateView.as_view(), name="update"),
    path("manage/<int:pk>/toggle/", views.AnnouncementToggleView.as_view(), name="toggle"),
    path("<int:pk>/", views.AnnouncementDetailView.as_view(), name="detail"),
]
