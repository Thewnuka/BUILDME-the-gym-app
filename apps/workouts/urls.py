from django.urls import path

from . import views

app_name = "workouts"

urlpatterns = [
    path("exercises/", views.ExerciseListView.as_view(), name="exercises"),
    path("exercises/new/", views.ExerciseCreateView.as_view(), name="exercise_create"),
    path("exercises/<int:pk>/", views.ExerciseDetailView.as_view(), name="exercise_detail"),
    path("exercises/<int:pk>/edit/", views.ExerciseUpdateView.as_view(), name="exercise_update"),
    path("exercises/<int:pk>/image/", views.ExerciseImageView.as_view(), name="exercise_image"),
    path("plans/", views.PlanListView.as_view(), name="plans"),
    path("plans/new/", views.PlanCreateView.as_view(), name="plan_create"),
    path("plans/<int:pk>/", views.PlanDetailView.as_view(), name="plan_detail"),
    path("plans/<int:pk>/edit/", views.PlanUpdateView.as_view(), name="plan_update"),
    path("plans/<int:pk>/days/", views.AddDayView.as_view(), name="day_add"),
    path("plans/<int:pk>/days/<int:day_pk>/delete/", views.DeleteDayView.as_view(), name="day_delete"),
    path("plans/<int:pk>/days/<int:day_pk>/items/", views.AddItemView.as_view(), name="item_add"),
    path("plans/<int:pk>/items/<int:item_pk>/", views.UpdateItemView.as_view(), name="item_update"),
    path("plans/<int:pk>/items/<int:item_pk>/delete/", views.DeleteItemView.as_view(), name="item_delete"),
    path("plans/<int:pk>/archive/", views.ArchivePlanView.as_view(), name="plan_archive"),
    path("plans/<int:pk>/assign/", views.AssignPlanView.as_view(), name="plan_assign"),
    path("plans/<int:pk>/days/<int:day_pk>/log/", views.LogWorkoutView.as_view(), name="log"),
    path("progress/<int:member_pk>/", views.ProgressView.as_view(), name="progress"),
    path("measurements/", views.MeasurementTypeView.as_view(), name="measurement_types"),
    path("my/", views.MyTrainingView.as_view(), name="my"),
    path("my/progress/", views.MyProgressView.as_view(), name="my_progress"),
]
