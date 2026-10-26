from django.urls import path

from . import views

app_name = "members"

urlpatterns = [
    path("", views.MemberListView.as_view(), name="list"),
    path("new/", views.MemberCreateView.as_view(), name="create"),
    path("<int:pk>/", views.MemberDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.MemberUpdateView.as_view(), name="update"),
    path("<int:pk>/deactivate/", views.MemberDeactivateView.as_view(), name="deactivate"),
    path("<int:pk>/reactivate/", views.MemberReactivateView.as_view(), name="reactivate"),
    path("<int:pk>/portal-login/", views.MemberPortalLoginView.as_view(), name="portal_login"),
    path("<int:pk>/photo/", views.MemberPhotoView.as_view(), name="photo"),
]
