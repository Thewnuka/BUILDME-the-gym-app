"""
External delivery channels: email, SMS and WhatsApp.

Providers are optional. Without credentials nothing is sent and the delivery
is recorded as "skipped" with the reason, so the app never claims a message
went out when it didn't. HTTP calls use the standard library (no extra deps).

Supported providers
  * Email    – Django's EMAIL_* settings (SMTP etc.). "sent" = accepted by the
               mail server; email has no delivery receipt.
  * SMS      – Twilio (queued → delivered/failed via status webhook) or
               Notify.lk (Sri Lanka; "sent" = accepted, no delivery receipts).
  * WhatsApp – Twilio WhatsApp (queued → delivered/read/failed via webhook).
"""
import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail import send_mail

from .models import NotificationDelivery

logger = logging.getLogger(__name__)
Status = NotificationDelivery.Status
DEV_EMAIL_BACKENDS = ("console", "dummy", "filebased")
TIMEOUT = 15


def _http(url, data=None, auth=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (fixed provider URLs)
        return json.loads(resp.read().decode() or "{}")


def _set(delivery, status, detail="", provider="", message_id=""):
    delivery.status = status
    delivery.detail = detail[:255]
    if provider:
        delivery.provider = provider
    if message_id:
        delivery.provider_message_id = message_id
    delivery.save()
    return delivery


def send_email(delivery, subject, body):
    if not delivery.recipient:
        return _set(delivery, Status.SKIPPED, "No email address")
    if any(name in settings.EMAIL_BACKEND for name in DEV_EMAIL_BACKENDS):
        return _set(delivery, Status.SKIPPED, "Development email backend: written to the console/log, not delivered",
                    provider="django")
    try:
        send_mail(subject, body, None, [delivery.recipient])
    except Exception as exc:  # SMTP errors etc.
        logger.warning("Email delivery failed: %s", exc)
        return _set(delivery, Status.FAILED, f"Mail server error: {exc.__class__.__name__}", provider="smtp")
    return _set(delivery, Status.SENT, "Accepted by the mail server (email has no delivery receipt)", provider="smtp")


def _twilio(delivery, to, body, from_number):
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and from_number):
        return _set(delivery, Status.SKIPPED, "Twilio credentials not configured", provider="twilio")
    data = {"To": to, "From": from_number, "Body": body[:1500]}
    if settings.PUBLIC_BASE_URL:
        data["StatusCallback"] = settings.PUBLIC_BASE_URL.rstrip("/") + "/notifications/webhooks/twilio/"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
    try:
        result = _http(url, data, auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
    except urllib.error.HTTPError as exc:
        return _set(delivery, Status.FAILED, f"Twilio rejected the message (HTTP {exc.code})", provider="twilio")
    except Exception as exc:
        return _set(delivery, Status.FAILED, f"Could not reach Twilio: {exc.__class__.__name__}", provider="twilio")
    status = result.get("status", "")
    if status in ("failed", "undelivered"):
        return _set(delivery, Status.FAILED, f"Twilio status: {status}", "twilio", result.get("sid", ""))
    note = "Accepted by Twilio; waiting for delivery receipt" if settings.PUBLIC_BASE_URL else \
        "Accepted by Twilio (no PUBLIC_BASE_URL, so delivery can't be confirmed)"
    return _set(delivery, Status.QUEUED, note, "twilio", result.get("sid", ""))


def _notifylk(delivery, to, body):
    if not (settings.NOTIFYLK_USER_ID and settings.NOTIFYLK_API_KEY):
        return _set(delivery, Status.SKIPPED, "Notify.lk credentials not configured", provider="notifylk")
    query = urllib.parse.urlencode({
        "user_id": settings.NOTIFYLK_USER_ID, "api_key": settings.NOTIFYLK_API_KEY,
        "sender_id": settings.NOTIFYLK_SENDER_ID, "to": to.lstrip("+"), "message": body[:600],
    })
    try:
        result = _http(f"https://app.notify.lk/api/v1/send?{query}")
    except Exception as exc:
        return _set(delivery, Status.FAILED, f"Could not reach Notify.lk: {exc.__class__.__name__}", provider="notifylk")
    if result.get("status") == "success":
        return _set(delivery, Status.SENT, "Accepted by Notify.lk (no delivery receipts)", provider="notifylk")
    return _set(delivery, Status.FAILED, f"Notify.lk error: {str(result.get('message', 'unknown'))[:120]}",
                provider="notifylk")


def send_sms(delivery, body):
    if not delivery.recipient:
        return _set(delivery, Status.SKIPPED, "No phone number")
    if settings.SMS_PROVIDER == "twilio":
        return _twilio(delivery, delivery.recipient, body, settings.TWILIO_SMS_FROM)
    if settings.SMS_PROVIDER == "notifylk":
        return _notifylk(delivery, delivery.recipient, body)
    return _set(delivery, Status.SKIPPED, "No SMS provider configured")


def send_whatsapp(delivery, body):
    if not delivery.recipient:
        return _set(delivery, Status.SKIPPED, "No phone number")
    if settings.WHATSAPP_PROVIDER == "twilio":
        sender = settings.TWILIO_WHATSAPP_FROM
        return _twilio(delivery, f"whatsapp:{delivery.recipient}", body,
                       f"whatsapp:{sender}" if sender and not sender.startswith("whatsapp:") else sender)
    return _set(delivery, Status.SKIPPED, "No WhatsApp provider configured")


def twilio_signature_valid(url, params, signature):
    """Twilio request validation: HMAC-SHA1 of URL + sorted POST params, base64."""
    if not settings.TWILIO_AUTH_TOKEN or not signature:
        return False
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(settings.TWILIO_AUTH_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


TWILIO_STATUS_MAP = {
    "queued": Status.QUEUED, "accepted": Status.QUEUED, "sending": Status.QUEUED, "sent": Status.SENT,
    "delivered": Status.DELIVERED, "read": Status.DELIVERED, "undelivered": Status.FAILED, "failed": Status.FAILED,
}
