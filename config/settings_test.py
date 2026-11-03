"""Settings for the test suite: same database engine (PostgreSQL), relaxed transport security."""

import os
import tempfile

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")
os.environ.setdefault("DJANGO_SECURE_SSL_REDIRECT", "0")

from .settings import *  # noqa: E402,F403

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MEDIA_ROOT = tempfile.mkdtemp(prefix="vc-test-media-")
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
MIDDLEWARE = [m for m in MIDDLEWARE if not m.startswith("whitenoise")]  # noqa: F405
LOGGING = {"version": 1, "disable_existing_loggers": False,
           "loggers": {"django.request": {"level": "CRITICAL"}}}
