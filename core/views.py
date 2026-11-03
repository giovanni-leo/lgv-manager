from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render

from .models import Company, Deal, Investment
from .services.ownership import consistency_warnings


def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


def home(request):
    """Placeholder dashboard for phase 1; the real home page arrives in phase 2-3."""
    context = {
        "n_companies": Company.objects.count(),
        "n_deals": Deal.objects.count(),
        "n_investments": Investment.objects.count(),
        "warnings": _named(consistency_warnings()),
    }
    return render(request, "core/home.html", context)


def _named(warnings):
    names = dict(Company.objects.filter(pk__in={w.company_id for w in warnings}).values_list("pk", "name"))
    return [{**w.__dict__, "company_name": names.get(w.company_id, "")} for w in warnings]


def robots(request):
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")
