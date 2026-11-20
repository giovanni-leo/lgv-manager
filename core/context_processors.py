from django.conf import settings


def site(request):
    return {"SITE_NAME": settings.SITE_NAME, "USER_MANUAL_URL": settings.USER_MANUAL_URL,
            "ai_enabled": settings.AI_ENABLED}
