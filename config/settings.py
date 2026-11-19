"""Django settings for VC Manager.

All environment-specific values come from environment variables so that the
same image runs locally and on the Swiss production server.
"""

import os
from pathlib import Path

from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY") or ("dev-insecure-key" if DEBUG else None)
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
# The container health check calls the app on localhost; only reachable from inside.
ALLOWED_HOSTS += [h for h in ("localhost", "127.0.0.1") if h not in ALLOWED_HOSTS]
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "https://localhost")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.postgres",
    # Two-factor authentication
    "django_otp",
    "django_otp.plugins.otp_static",
    "django_otp.plugins.otp_totp",
    "two_factor",
    # Utilities
    "django_htmx",
    "auditlog",
    # Domain
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "core.middleware.RequireTwoFactorMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "auditlog.middleware.AuditlogMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "vc"),
        "USER": env("POSTGRES_USER", "vc"),
        "PASSWORD": env("POSTGRES_PASSWORD", "vc"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Name shown in the interface and in authenticator apps (one installation per fund).
SITE_NAME = env("SITE_NAME", "VC Manager")
# "Manual" in the menu shows docs/USER_GUIDE.md; set a URL to open an external copy instead.
USER_MANUAL_URL = env("USER_MANUAL_URL", "")

# --- Authentication / 2FA ----------------------------------------------------
LOGIN_URL = "two_factor:login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "two_factor:login"
# When true every page except login/2FA setup requires an OTP-verified session.
REQUIRE_2FA = env_bool("REQUIRE_2FA", True)
OTP_TOTP_ISSUER = env("OTP_TOTP_ISSUER", SITE_NAME)
TWO_FACTOR_REMEMBER_COOKIE_AGE = None  # never skip the second factor

# --- Internationalisation -----------------------------------------------------
# The interface is in English. Message ids are English; Italian translations are
# kept in locale/ so another language can be enabled later by adding it to
# LANGUAGES (users would then get it from their browser settings).
LANGUAGE_CODE = "en"
LANGUAGES = [("en", _("English"))]
LOCALE_PATHS = [BASE_DIR / "locale"]
# English texts with European date conventions (3 Nov 2026, 24-hour clock).
FORMAT_MODULE_PATH = ["config.formats"]
TIME_ZONE = "Europe/Zurich"
USE_I18N = True
USE_TZ = True

# --- Money --------------------------------------------------------------------
BASE_CURRENCY = "CHF"
CURRENCIES = ["CHF", "EUR", "USD"]

# --- Claude (automatic data gathering) ------------------------------------------
# The API key is read by the Anthropic SDK from ANTHROPIC_API_KEY. Without it the
# "Search the web" button and email analysis are hidden / skipped.
AI_ENABLED = bool(env("ANTHROPIC_API_KEY")) and env_bool("AI_ENABLED", True)
AI_MODEL = env("AI_MODEL", "claude-opus-5-5")
AI_RESEARCH_EFFORT = env("AI_RESEARCH_EFFORT", "high")
AI_EXTRACTION_EFFORT = env("AI_EXTRACTION_EFFORT", "medium")
AI_MAX_WEB_SEARCHES = int(env("AI_MAX_WEB_SEARCHES", "12"))
# Send email attachments (decks, term sheets) to Claude for reading.
AI_READ_ATTACHMENTS = env_bool("AI_READ_ATTACHMENTS", True)
# Model prices in USD per million tokens, only used to show an approximate cost per job.
AI_PRICE_INPUT_PER_MTOK = float(env("AI_PRICE_INPUT_PER_MTOK", "4"))
AI_PRICE_OUTPUT_PER_MTOK = float(env("AI_PRICE_OUTPUT_PER_MTOK", "20"))

# --- Email intake ------------------------------------------------------------------
# A dedicated mailbox read over IMAP by the worker. Only senders listed in
# MAIL_ALLOWED_SENDERS (addresses or @domains) are processed.
MAIL_IMAP_HOST = env("MAIL_IMAP_HOST", "")
MAIL_IMAP_PORT = int(env("MAIL_IMAP_PORT", "993"))
MAIL_IMAP_USER = env("MAIL_IMAP_USER", "")
MAIL_IMAP_PASSWORD = env("MAIL_IMAP_PASSWORD", "").replace(" ", "")  # Google shows app passwords in groups of 4
MAIL_IMAP_FOLDER = env("MAIL_IMAP_FOLDER", "INBOX")
# The receiving server whose Authentication-Results header proves the sender is genuine;
# empty switches the check off. Gmail mailboxes: mx.google.com.
MAIL_AUTH_SERVER = env("MAIL_AUTH_SERVER", "mx.google.com" if MAIL_IMAP_HOST == "imap.gmail.com" else "")
MAIL_ALLOWED_SENDERS = [s.lower() for s in env_list("MAIL_ALLOWED_SENDERS", "")]
MAIL_POLL_SECONDS = int(env("MAIL_POLL_SECONDS", "120"))
MAIL_MAX_ATTACHMENT_MB = int(env("MAIL_MAX_ATTACHMENT_MB", "25"))

# --- Static and uploaded files -----------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = Path(env("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles"))
STATICFILES_DIRS = [BASE_DIR / "static"]
# Uploaded files live outside the database; MEDIA_URL is intentionally not
# exposed by the reverse proxy: files are only served by authenticated views.
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT", BASE_DIR / "media"))
MEDIA_URL = "/files/"
DOCUMENT_MAX_UPLOAD_MB = int(env("DOCUMENT_MAX_UPLOAD_MB", "100"))
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# --- Security -----------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 12
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SECURE_HSTS_SECONDS = 0 if DEBUG else 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "SAMEORIGIN"  # same-origin iframes are needed for PDF previews

# --- Audit log ----------------------------------------------------------------
AUDITLOG_INCLUDE_ALL_MODELS = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
}
