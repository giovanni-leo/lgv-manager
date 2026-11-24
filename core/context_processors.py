from django.conf import settings

from .version import version_string


def site(request):
    from .funds import active_fund
    from .models import InvestingEntity

    user = getattr(request, "user", None)
    show_funds = bool(user and user.is_authenticated and getattr(user, "is_verified", lambda: False)())
    funds = list(InvestingEntity.objects.all()) if show_funds else []
    return {"funds": funds, "active_fund": active_fund(request) if funds else None, "SITE_NAME": settings.SITE_NAME, "USER_MANUAL_URL": settings.USER_MANUAL_URL,
            "ai_enabled": settings.AI_ENABLED, "APP_VERSION": version_string()}
