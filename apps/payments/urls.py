from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.PaymentListView.as_view(), name="list"),
    path("outstanding/", views.OutstandingView.as_view(), name="outstanding"),
    path("record/<int:membership_pk>/", views.RecordPaymentView.as_view(), name="record"),
    path("record/session/<int:appointment_pk>/", views.RecordPaymentView.as_view(), name="record_session"),
    path("adjust/<int:membership_pk>/", views.AdjustBalanceView.as_view(), name="adjust"),
    path("<int:pk>/", views.PaymentDetailView.as_view(), name="detail"),
    path("<int:pk>/confirm/", views.ConfirmPaymentView.as_view(), name="confirm"),
    path("<int:pk>/refund/", views.RefundView.as_view(), name="refund"),
    path("<int:pk>/void/", views.VoidPaymentView.as_view(), name="void"),
    path("<int:pk>/receipt/", views.ReceiptView.as_view(), name="receipt"),
    path("<int:pk>/receipt.pdf", views.ReceiptPDFView.as_view(), name="receipt_pdf"),
]
