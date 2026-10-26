from django.urls import path

from . import views

app_name = "nutrition"

urlpatterns = [
    path("plans/", views.MealPlanListView.as_view(), name="plans"),
    path("plans/new/", views.MealPlanCreateView.as_view(), name="plan_create"),
    path("plans/<int:pk>/", views.MealPlanDetailView.as_view(), name="plan_detail"),
    path("plans/<int:pk>/meals/", views.AddMealView.as_view(), name="meal_add"),
    path("plans/<int:pk>/meals/<int:meal_pk>/delete/", views.DeleteMealView.as_view(), name="meal_delete"),
    path("plans/<int:pk>/assign/", views.AssignMealPlanView.as_view(), name="plan_assign"),
    path("member/<int:member_pk>/", views.MemberNutritionView.as_view(), name="member"),
    path("my/", views.MyNutritionView.as_view(), name="my"),
]
