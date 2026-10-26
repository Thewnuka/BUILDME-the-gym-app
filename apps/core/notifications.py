from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import ListView

from apps.core.mixins import ModulePermissionMixin, PageMetaMixin

from . import channels
from .models import Notification, NotificationDelivery


class NotificationListView(PageMetaMixin, ListView):
    """Notification centre: every user sees only their own notifications."""

    template_name = "core/notifications.html"
    context_object_name = "notifications"
    paginate_by = 30
    page_title = "Notifications"

    def get_queryset(self):
        self.show = self.request.GET.get("show", "all") if self.request.GET.get("show") in ("all", "unread") else "all"
        self.category = self.request.GET.get("category", "")
        qs = Notification.objects.filter(user=self.request.user)
        if self.show == "unread":
            qs = qs.filter(read_at__isnull=True)
        if self.category in Notification.Category.values:
            qs = qs.filter(category=self.category)
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(show=self.show, category=self.category,
                                        categories=Notification.Category.choices, **kwargs)


class NotificationOpenView(View):
    http_method_names = ["post"]

    def post(self, request, pk):
        note = get_object_or_404(Notification, pk=pk, user=request.user)  # ownership
        if note.read_at is None:
            note.read_at = timezone.now()
            note.save(update_fields=["read_at"])
        target = note.link if note.link and url_has_allowed_host_and_scheme(note.link, {request.get_host()}) else None
        return redirect(target or "notifications:list")


class NotificationReadAllView(View):
    http_method_names = ["post"]

    def post(self, request):
        Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
        return redirect("notifications:list")


class DeliveryLogView(ModulePermissionMixin, PageMetaMixin, ListView):
    """What happened to every external email/SMS/WhatsApp attempt."""

    permission_required = "accounts.view_notification_log"
    template_name = "core/deliveries.html"
    context_object_name = "deliveries"
    paginate_by = 50
    page_title = "Message Delivery Log"

    def get_queryset(self):
        self.status = self.request.GET.get("status", "")
        self.channel = self.request.GET.get("channel", "")
        qs = NotificationDelivery.objects.select_related("notification")
        if self.status in NotificationDelivery.Status.values:
            qs = qs.filter(status=self.status)
        if self.channel in NotificationDelivery.Channel.values:
            qs = qs.filter(channel=self.channel)
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            status=self.status, channel=self.channel, statuses=NotificationDelivery.Status.choices,
            channels_=NotificationDelivery.Channel.choices, configured=settings.NOTIFY_CHANNELS,
            sms_provider=settings.SMS_PROVIDER, whatsapp_provider=settings.WHATSAPP_PROVIDER,
            email_backend=settings.EMAIL_BACKEND.rsplit(".", 2)[-2], **kwargs)


STATUS_RANK = {"skipped": 0, "queued": 1, "sent": 2, "failed": 3, "delivered": 3}


@method_decorator(login_not_required, name="dispatch")
@method_decorator(csrf_exempt, name="dispatch")
class TwilioStatusWebhook(View):
    """Twilio delivery receipts. Requests are verified with Twilio's signature."""

    http_method_names = ["post"]

    def post(self, request):
        url = (settings.PUBLIC_BASE_URL.rstrip("/") + request.path) if settings.PUBLIC_BASE_URL \
            else request.build_absolute_uri()
        params = {k: request.POST.get(k) for k in request.POST}
        if not channels.twilio_signature_valid(url, params, request.headers.get("X-Twilio-Signature", "")):
            return HttpResponseForbidden("Invalid signature")
        sid, raw = params.get("MessageSid", ""), params.get("MessageStatus", "")
        new_status = channels.TWILIO_STATUS_MAP.get(raw)
        delivery = NotificationDelivery.objects.filter(provider="twilio", provider_message_id=sid).first()
        if delivery and new_status and STATUS_RANK[new_status] >= STATUS_RANK.get(delivery.status, 0):
            detail = f"Twilio: {raw}" + (f" (error {params['ErrorCode']})" if params.get("ErrorCode") else "")
            channels._set(delivery, new_status, detail)
        return HttpResponse(status=204)
