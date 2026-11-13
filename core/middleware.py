"""Every page requires a logged-in user who passed the second factor."""

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django_otp import user_has_device

# Paths reachable without a verified session.
PUBLIC_PREFIXES = ("/account/login/", "/healthz", "/static/", "/i18n/", "/favicon.ico", "/robots.txt")
# Paths reachable when logged in with a password but no second factor yet.
SETUP_PREFIXES = ("/account/two_factor/setup/", "/account/two_factor/qrcode/", "/account/logout/")
# Sec-Fetch-Dest values of a page the user navigated to (absent in old browsers and tests).
NAVIGATION_DESTS = {None, "document", "iframe", "frame"}


def _send_to(request, url):
    """Redirect page navigations only.

    Browsers fetch icons, images and scripts on their own. Redirecting those to
    the login or 2FA setup wizard would restart the wizard in the background
    and invalidate the key the user is scanning, so they are simply refused.
    """
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    if request.headers.get("Sec-Fetch-Dest") not in NAVIGATION_DESTS:
        return HttpResponseForbidden()
    return redirect(url)


class RequireTwoFactorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if not path.startswith(PUBLIC_PREFIXES):
            user = request.user
            login_url = f"{reverse('two_factor:login')}?next={request.get_full_path()}"
            if not user.is_authenticated:
                return _send_to(request, login_url)
            if getattr(settings, "REQUIRE_2FA", True) and not user.is_verified():
                if path.startswith(SETUP_PREFIXES):
                    return self.get_response(request)
                if user_has_device(user):
                    # Password-only session (e.g. after a device was added elsewhere).
                    return _send_to(request, login_url)
                return _send_to(request, reverse("two_factor:setup"))
        return self.get_response(request)
