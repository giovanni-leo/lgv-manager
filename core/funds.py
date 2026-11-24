"""The fund selected in the top bar (kept in the session) and the funds a user can choose from."""

from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import InvestingEntity

SESSION_KEY = "fund_id"


def active_fund(request):
    """The selected fund, or None for all funds."""
    pk = request.session.get(SESSION_KEY)
    return InvestingEntity.objects.filter(pk=pk).first() if pk else None


@require_POST
def fund_select(request):
    fund_id = request.POST.get("fund", "")
    if fund_id.isdigit() and InvestingEntity.objects.filter(pk=int(fund_id)).exists():
        request.session[SESSION_KEY] = int(fund_id)
    else:
        request.session.pop(SESSION_KEY, None)
    target = request.POST.get("next") or "/"
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = "/"
    return redirect(target)
