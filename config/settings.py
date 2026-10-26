"""
Django settings for the Gym Management System.

All secrets and environment-specific values are read from environment
variables (optionally loaded from a local `.env` file). See `.env.example`.
"""
import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        # Development-only fallback so a fresh checkout runs out of the box.
        SECRET_KEY = "django-insecure-dev-only-key-change-me"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Brand name shown in the header, page titles and emails.
GYM_NAME = env("GYM_NAME", "BUILDME")

# Allow visitors to create their own (Member) accounts from the login page.
REGISTRATION_ENABLED = env_bool("REGISTRATION_ENABLED", True)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Project apps
    "apps.core",
    "apps.accounts",
    "apps.dashboard",
    "apps.members",
    "apps.memberships",
    "apps.payments",
    "apps.attendance",
    "apps.trainers",
    "apps.appointments",
    "apps.workouts",
    "apps.nutrition",
    "apps.expenses",
    "apps.equipment",
    "apps.inventory",
    "apps.announcements",
    "apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves collected static files efficiently (compressed, cache-busted names).
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Every view requires authentication unless explicitly marked public
    # with @login_not_required.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
                "apps.core.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --------------------------------------------------------------------------
# Database: SQLite locally, PostgreSQL in production via DATABASE_URL
# e.g. postgres://user:password@host:5432/gym_db
# --------------------------------------------------------------------------
DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL") or f"sqlite:///{(BASE_DIR / 'db.sqlite3').as_posix()}",
        conn_max_age=int(env("DB_CONN_MAX_AGE", "60")),
        conn_health_checks=True,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.UsernameOrEmailBackend",
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_RESET_TIMEOUT = 60 * 60 * 24  # 24 hours

if "test" in sys.argv[1:2]:
    # Fast hashing keeps the test suite quick; never used outside tests.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Brute-force protection for the login form.
LOGIN_MAX_ATTEMPTS = int(env("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_SECONDS = int(env("LOGIN_LOCKOUT_SECONDS", "900"))

SESSION_COOKIE_AGE = int(env("SESSION_COOKIE_AGE", str(60 * 60 * 12)))
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# --------------------------------------------------------------------------
# Security (hardened automatically when DEBUG is off)
# --------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
    CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", True)
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
    if env_bool("USE_X_FORWARDED_PROTO", False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --------------------------------------------------------------------------
# Cache (used for login throttling). Use a shared cache such as Redis or
# the database cache in multi-process production deployments.
# --------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": env("CACHE_BACKEND", "django.core.cache.backends.locmem.LocMemCache"),
        "LOCATION": env("CACHE_LOCATION", "gym-default"),
    }
}

# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "no-reply@gym.local")

# --------------------------------------------------------------------------
# Internationalization
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "Asia/Colombo")
USE_I18N = True
USE_TZ = True

CURRENCY_SYMBOL = env("CURRENCY_SYMBOL", "LKR")

# --------------------------------------------------------------------------
# Members & memberships
# --------------------------------------------------------------------------
MEMBER_ID_PREFIX = env("MEMBER_ID_PREFIX", "BM")
# Local numbers starting with 0 are normalised to +<code> for duplicate checks.
PHONE_DEFAULT_COUNTRY_CODE = env("PHONE_DEFAULT_COUNTRY_CODE", "94")
MEMBER_PHOTO_MAX_BYTES = int(env("MEMBER_PHOTO_MAX_KB", "2048")) * 1024
MEMBERSHIP_EXPIRING_SOON_DAYS = int(env("MEMBERSHIP_EXPIRING_SOON_DAYS", "7"))
# How far back a new membership may be back-dated (for migrating existing members).
MEMBERSHIP_MAX_BACKDATE_DAYS = int(env("MEMBERSHIP_MAX_BACKDATE_DAYS", "31"))
MEMBERSHIP_MAX_ADVANCE_DAYS = int(env("MEMBERSHIP_MAX_ADVANCE_DAYS", "90"))

# --------------------------------------------------------------------------
# Payments & attendance
# --------------------------------------------------------------------------
# How far back a payment date may be entered (for recording late paperwork).
PAYMENT_MAX_BACKDATE_DAYS = int(env("PAYMENT_MAX_BACKDATE_DAYS", "365"))
# Prefix inside check-in QR codes. The QR holds only this and a random token.
ATTENDANCE_QR_PREFIX = env("ATTENDANCE_QR_PREFIX", "BUILDME-CHECKIN")

# --------------------------------------------------------------------------
# Training (Phase 4)
# --------------------------------------------------------------------------
# Members may cancel or reschedule their own sessions online up to this many
# hours before the start; later changes must be made by staff.
APPOINTMENT_MEMBER_CHANGE_CUTOFF_HOURS = int(env("APPOINTMENT_MEMBER_CHANGE_CUTOFF_HOURS", "24"))
APPOINTMENT_BOOKING_HORIZON_DAYS = int(env("APPOINTMENT_BOOKING_HORIZON_DAYS", "30"))
APPOINTMENT_SLOT_STEP_MINUTES = int(env("APPOINTMENT_SLOT_STEP_MINUTES", "30"))
APPOINTMENT_REMINDER_HOURS = int(env("APPOINTMENT_REMINDER_HOURS", "24"))
TRAINER_FILE_MAX_BYTES = int(env("TRAINER_FILE_MAX_KB", "5120")) * 1024
# The nutrition module can be switched off entirely (pages return 404, menu hidden).
NUTRITION_ENABLED = env_bool("NUTRITION_ENABLED", True)

# --------------------------------------------------------------------------
# Operations, notifications and messaging (Phase 5)
# --------------------------------------------------------------------------
EXPENSE_RECEIPT_MAX_BYTES = int(env("EXPENSE_RECEIPT_MAX_KB", "5120")) * 1024
# Reminder periods (days). Comma-separated lists send one reminder per value.
MEMBERSHIP_EXPIRY_REMINDER_DAYS = [int(d) for d in env_list("MEMBERSHIP_EXPIRY_REMINDER_DAYS", "7,1")]
PAYMENT_DUE_REMINDER_DAYS = int(env("PAYMENT_DUE_REMINDER_DAYS", "3"))
EQUIPMENT_MAINTENANCE_REMINDER_DAYS = int(env("EQUIPMENT_MAINTENANCE_REMINDER_DAYS", "7"))

# External channels for member-facing notifications. Empty = in-app only.
# Any of: email, sms, whatsapp. Nothing is sent without provider credentials.
NOTIFY_CHANNELS = env_list("NOTIFY_CHANNELS", "")
SMS_PROVIDER = env("SMS_PROVIDER", "")            # "", "twilio" or "notifylk"
WHATSAPP_PROVIDER = env("WHATSAPP_PROVIDER", "")  # "" or "twilio"
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN", "")
TWILIO_SMS_FROM = env("TWILIO_SMS_FROM", "")
TWILIO_WHATSAPP_FROM = env("TWILIO_WHATSAPP_FROM", "")
# Public base URL (https://…) so Twilio can post delivery receipts back.
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "")
NOTIFYLK_USER_ID = env("NOTIFYLK_USER_ID", "")
NOTIFYLK_API_KEY = env("NOTIFYLK_API_KEY", "")
NOTIFYLK_SENDER_ID = env("NOTIFYLK_SENDER_ID", "NotifyDEMO")

# Optional Celery (only used if installed and a broker is configured).
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "")
CELERY_TIMEZONE = TIME_ZONE

# --------------------------------------------------------------------------
# Static and media files
# --------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(env("STATIC_ROOT", "")) if env("STATIC_ROOT", "") else BASE_DIR / "staticfiles"
# Production: run `collectstatic`; file names get a content hash so browsers
# always load the current CSS/JS. Development and tests use plain storage.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
                    if not DEBUG and "test" not in sys.argv[1:2]
                    else "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Uploaded files. Keep MEDIA_ROOT private (outside the web root, never served by
# the proxy): every upload is delivered by a view that checks permissions.
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", "")) if env("MEDIA_ROOT", "") else BASE_DIR / "media"

ADMIN_URL = env("DJANGO_ADMIN_URL", "admin/")

# --------------------------------------------------------------------------
# Logging: errors are logged server-side, never shown to end users.
# --------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
