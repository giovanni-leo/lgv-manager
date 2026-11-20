from django.conf import settings

from .version import version_string


def site(request):
    return {"SITE_NAME": settings.SITE_NAME, "USER_MANUAL_URL": settings.USER_MANUAL_URL,
            "ai_enabled": settings.AI_ENABLED, "APP_VERSION": version_string()}
