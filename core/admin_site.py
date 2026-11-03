"""Admin site: requires an OTP-verified session; menu ordered by daily use."""

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from two_factor.admin import AdminSiteOTPRequiredMixin

# Most used first; models not listed follow in alphabetical order.
MODEL_ORDER = [
    "Company", "Deal", "Investment", "Realization", "Valuation", "CompanyMetric", "Milestone",
    "Interaction", "Document", "Contact", "ShareClass", "ShareEvent", "OptionPool",
    "ConvertibleTerms", "InvestingEntity", "FxRate",
]
APP_ORDER = ["core", "auth", "auditlog", "otp_totp", "otp_static"]


def _rank(order, key):
    return (order.index(key), "") if key in order else (len(order), key)


class OrderedMenuMixin:
    def get_app_list(self, request, app_label=None):
        apps = super().get_app_list(request, app_label)
        apps.sort(key=lambda app: _rank(APP_ORDER, app["app_label"]))
        for app in apps:
            if app["app_label"] == "core":
                app["models"].sort(key=lambda m: _rank(MODEL_ORDER, m["object_name"]))
        return apps


def patch_admin_site(site):
    if not isinstance(site, AdminSiteOTPRequiredMixin):
        site.__class__ = type(
            "VCAdminSite", (OrderedMenuMixin, AdminSiteOTPRequiredMixin, site.__class__), {}
        )
    site.site_header = settings.SITE_NAME
    site.site_title = settings.SITE_NAME
    site.index_title = _("Data")
    return site
