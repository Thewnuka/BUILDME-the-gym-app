from django.urls import path

from . import notifications

app_name = "notifications"

urlpatterns = [
    path("", notifications.NotificationListView.as_view(), name="list"),
    path("<int:pk>/open/", notifications.NotificationOpenView.as_view(), name="open"),
    path("read-all/", notifications.NotificationReadAllView.as_view(), name="read_all"),
    path("deliveries/", notifications.DeliveryLogView.as_view(), name="deliveries"),
    path("webhooks/twilio/", notifications.TwilioStatusWebhook.as_view(), name="twilio_webhook"),
]
