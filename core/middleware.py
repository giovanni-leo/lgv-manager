"""Every page requires a logged-in user who passed the second factor."""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django_otp import user_has_device

# Paths reachable without a verified session.
PUBLIC_PREFIXES = ("/account/login/", "/healthz", "/static/", "/i18n/")
# Paths reachable when logged in with a password but no second factor yet.
SETUP_PREFIXES = ("/account/two_factor/setup/", "/account/two_factor/qrcode/", "/account/logout/")


class RequireTwoFactorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if not path.startswith(PUBLIC_PREFIXES):
            user = request.user
            if not user.is_authenticated:
                return redirect(f"{reverse('two_factor:login')}?next={request.get_full_path()}")
            if getattr(settings, "REQUIRE_2FA", True) and not user.is_verified():
                if path.startswith(SETUP_PREFIXES):
                    return self.get_response(request)
                if user_has_device(user):
                    # Password-only session (e.g. after a device was added elsewhere).
                    return redirect(f"{reverse('two_factor:login')}?next={request.get_full_path()}")
                return redirect("two_factor:setup")
        return self.get_response(request)
