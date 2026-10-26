from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.ItemListView.as_view(), name="list"),
    path("new/", views.ItemCreateView.as_view(), name="create"),
    path("<int:pk>/", views.ItemDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.ItemUpdateView.as_view(), name="update"),
    path("<int:pk>/movement/", views.RecordMovementView.as_view(), name="movement"),
]
