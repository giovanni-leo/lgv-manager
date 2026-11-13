"""Two-factor setup views that show the fund name (SITE_NAME) in authenticator apps.

The library uses the request host as issuer, which shows up as "localhost" or
the bare domain in the user's authenticator app.
"""

from django.conf import settings
from two_factor.utils import get_otpauth_url, totp_digits
from two_factor.views import QRGeneratorView, SetupView


class FundSetupView(SetupView):
    def get_prefix(self, request, *args, **kwargs):
        return "setup_view"  # same form field names and session storage as the library view

    def get_context_data(self, form, **kwargs):
        context = super().get_context_data(form, **kwargs)
        if "secret_key" in context:
            context["issuer"] = settings.SITE_NAME
            context["otpauth_url"] = get_otpauth_url(
                accountname=self.request.user.get_username(), issuer=settings.SITE_NAME,
                secret=context["secret_key"], digits=totp_digits(),
            )
        return context


class FundQRGeneratorView(QRGeneratorView):
    def get_issuer(self):
        return settings.SITE_NAME
