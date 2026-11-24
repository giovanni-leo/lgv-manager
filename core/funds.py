"""The fund selected in the top bar (kept in the session) and the funds a user can choose from."""

from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import InvestingEntity

SESSION_KEY = "fund_id"
ALL = "all"


def ordered_funds():
    return InvestingEntity.objects.order_by("code", "name")


def active_fund(request):
    """The selected fund. None means "all funds", chosen explicitly: until someone chooses, the first fund is
    shown, so that the figures of two funds are not mixed on one page by default."""
    chosen = request.session.get(SESSION_KEY)
    if chosen == ALL:
        return None
    if isinstance(chosen, int):
        fund = InvestingEntity.objects.filter(pk=chosen).first()
        if fund:
            return fund
    return ordered_funds().first()


@require_POST
def fund_select(request):
    fund_id = request.POST.get("fund", "")
    if fund_id.isdigit() and InvestingEntity.objects.filter(pk=int(fund_id)).exists():
        request.session[SESSION_KEY] = int(fund_id)
    else:
        request.session[SESSION_KEY] = ALL
    target = request.POST.get("next") or "/"
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = "/"
    return redirect(target)


def fund_company_ids(fund):
    """Companies the fund has money in, or deals with."""
    from django.db.models import Q

    from .models import Company

    return Company.objects.filter(Q(investments__entity=fund) | Q(realizations__entity=fund) | Q(deals__fund=fund)
                                  ).values_list("pk", flat=True).distinct()


def visible_company_ids(fund):
    """Companies shown when ``fund`` is selected: those of the fund, and those not tied to any fund yet
    (new companies that are still being looked at)."""
    from django.db.models import Q

    from .models import Company

    tied = set(Company.objects.filter(Q(investments__isnull=False) | Q(realizations__isnull=False)
                                      | Q(deals__fund__isnull=False)).values_list("pk", flat=True))
    everyone = set(Company.objects.values_list("pk", flat=True))
    return set(fund_company_ids(fund)) | (everyone - tied)
