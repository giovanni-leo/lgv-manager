from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView
from two_factor.urls import urlpatterns as tf_urls

from core import views
from core.views_auth import FundQRGeneratorView, FundSetupView
from core.admin_site import patch_admin_site

patch_admin_site(admin.site)

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("robots.txt", views.robots),
    path("i18n/", include("django.conf.urls.i18n")),
    path("favicon.ico", RedirectView.as_view(url=static("favicon.svg"), permanent=True)),
    path("account/logout/", LogoutView.as_view(), name="logout"),
    # Same URLs as two_factor's own views, matched first, to show the fund name.
    path("account/two_factor/setup/", FundSetupView.as_view()),
    path("account/two_factor/qrcode/", FundQRGeneratorView.as_view()),
    path("", include(tf_urls)),  # /account/login/, /account/two_factor/...
    path("admin/", admin.site.urls),
    path("", views.home, name="home"),
]
