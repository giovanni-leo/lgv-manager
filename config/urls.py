from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView
from two_factor.urls import urlpatterns as tf_urls

from core import views, views_ai, views_app, views_rounds
from core.views_auth import FundQRGeneratorView, FundSetupView
from core.admin_site import patch_admin_site

patch_admin_site(admin.site)

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("robots.txt", views.robots),
    path("files/<path:path>", views.document_file, name="document_file"),
    path("i18n/", include("django.conf.urls.i18n")),
    path("favicon.ico", RedirectView.as_view(url=static("favicon.svg"), permanent=True)),
    path("account/logout/", LogoutView.as_view(), name="logout"),
    # Same URLs as two_factor's own views, matched first, to show the fund name.
    path("account/two_factor/setup/", FundSetupView.as_view()),
    path("account/two_factor/qrcode/", FundQRGeneratorView.as_view()),
    path("", include(tf_urls)),  # /account/login/, /account/two_factor/...
    path("admin/", admin.site.urls),
    path("", views_app.home, name="home"),
    path("pipeline/", views_app.pipeline, name="pipeline"),
    path("deals/new/", views_app.deal_new, name="deal_new"),
    path("deals/<int:pk>/", views_app.deal_detail, name="deal_detail"),
    path("deals/<int:pk>/stage/", views_app.deal_stage, name="deal_stage"),
    path("deals/<int:pk>/participants/", views_app.participant_add, name="participant_add"),
    path("participants/<int:pk>/delete/", views_app.participant_delete, name="participant_delete"),
    path("interactions/add/", views_app.interaction_add, name="interaction_add"),
    path("portfolio/", views_app.portfolio, name="portfolio"),
    path("companies/<int:pk>/", views_app.company_detail, name="company_detail"),
    path("companies/<int:company_pk>/rounds/new/", views_rounds.record_round_view, name="record_round"),
    path("s/<str:kind>/<int:pk>/<str:name>/", views_app.section, name="section"),
    path("companies/<int:company_pk>/suggestions/", views_ai.suggestions_card, name="suggestions_card"),
    path("companies/<int:company_pk>/research/", views_ai.research_start, name="research_start"),
    path("companies/<int:company_pk>/suggestions/<str:action>-all/", views_ai.suggestions_decide_all,
         name="suggestions_decide_all"),
    path("suggestions/<int:pk>/<str:action>/", views_ai.suggestion_decide, name="suggestion_decide"),
    path("inbox/", views_ai.inbox, name="inbox"),
    path("inbox/<int:pk>/reprocess/", views_ai.inbox_reprocess, name="inbox_reprocess"),
]
