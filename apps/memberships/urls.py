from django.urls import path

from . import views

app_name = "memberships"

urlpatterns = [
    path("", views.MembershipListView.as_view(), name="list"),
    path("expiring/", views.ExpiringSoonView.as_view(), name="expiring"),
    path("expired/", views.ExpiredView.as_view(), name="expired"),
    path("assign/<int:member_pk>/", views.AssignMembershipView.as_view(), name="assign"),
    path("<int:pk>/", views.MembershipDetailView.as_view(), name="detail"),
    path("<int:pk>/renew/", views.RenewMembershipView.as_view(), name="renew"),
    path("<int:pk>/suspend/", views.SuspendMembershipView.as_view(), name="suspend"),
    path("<int:pk>/reactivate/", views.ReactivateMembershipView.as_view(), name="reactivate"),
    path("<int:pk>/cancel/", views.CancelMembershipView.as_view(), name="cancel"),
    path("plans/", views.PlanListView.as_view(), name="plan_list"),
    path("plans/new/", views.PlanCreateView.as_view(), name="plan_create"),
    path("plans/<int:pk>/", views.PlanDetailView.as_view(), name="plan_detail"),
    path("plans/<int:pk>/edit/", views.PlanUpdateView.as_view(), name="plan_update"),
    path("plans/<int:pk>/toggle-active/", views.PlanToggleActiveView.as_view(), name="plan_toggle_active"),
]
