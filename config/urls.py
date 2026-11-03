from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from two_factor.urls import urlpatterns as tf_urls

from core import views
from core.admin_site import patch_admin_site

patch_admin_site(admin.site)

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("robots.txt", views.robots),
    path("i18n/", include("django.conf.urls.i18n")),
    path("account/logout/", LogoutView.as_view(), name="logout"),
    path("", include(tf_urls)),  # /account/login/, /account/two_factor/...
    path("admin/", admin.site.urls),
    path("", views.home, name="home"),
]
