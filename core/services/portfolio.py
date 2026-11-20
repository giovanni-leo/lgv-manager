"""At-a-glance figures for a deal (prospect phase) and a portfolio company."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import CompanyMetric, Interaction, RoundParticipant, Valuation

from .fx import to_chf
from .ownership import ownership_by_class, ownership_company

RUNWAY_WARNING_MONTHS = Decimal(12)
RUNWAY_CRITICAL_MONTHS = Decimal(6)
DAYS_PER_MONTH = Decimal("30.4375")
REPORT_DOCUMENT_CATEGORIES = ["investor_report", "financials", "board_minutes", "board_material", "general_assembly",
                              "cap_table"]


# --- IRR ----------------------------------------------------------------------

def xirr(flows):
    """Annual internal rate of return of dated cash flows [(date, amount)], or None.

    Negative amounts are money invested, positive amounts money received (or
    the value still held at the end). Solved by bisection, which always
    converges when the flows change sign once, the normal case for an
    investment.
    """
    flows = [(d, float(a)) for d, a in flows if a]
    if not flows or not any(a < 0 for _d, a in flows) or not any(a > 0 for _d, a in flows):
        return None
    start = min(d for d, _a in flows)

    def npv(rate):
        return sum(a / (1 + rate) ** ((d - start).days / 365.0) for d, a in flows)

    low, high = -0.9999, 100.0
    f_low, f_high = npv(low), npv(high)
    if f_low * f_high > 0:
        return None
    for _iteration in range(200):
        mid = (low + high) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7:
            break
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return Decimal(str(round((low + high) / 2, 6)))


# --- Runway -------------------------------------------------------------------

@dataclass
class Runway:
    metric: CompanyMetric
    months: Decimal | None

    @property
    def level(self):
        if self.months is None:
            return None
        if self.months < RUNWAY_CRITICAL_MONTHS:
            return "critical"
        if self.months < RUNWAY_WARNING_MONTHS:
            return "warning"
        return "ok"


def runway(company, as_of=None):
    """Cash / monthly burn, less the months elapsed since the figures were reported."""
    as_of = as_of or timezone.localdate()
    metric = company.metrics.filter(as_of__lte=as_of).order_by("-as_of").first()
    if metric is None:
        return None
    months = None
    if metric.cash is not None and metric.monthly_burn:
        elapsed = Decimal((as_of - metric.as_of).days) / DAYS_PER_MONTH
        months = max(Decimal(0), metric.cash / metric.monthly_burn - elapsed).quantize(Decimal("0.1"))
    return Runway(metric=metric, months=months)


# --- Portfolio company ----------------------------------------------------------

@dataclass
class CompanySummary:
    company: object
    as_of: date
    ownership: object = None
    classes: list = field(default_factory=list)
    invested_chf: Decimal | None = None
    realized_chf: Decimal = Decimal(0)
    valuation: Valuation | None = None
    company_value: Decimal | None = None          # in valuation currency
    stake_value: Decimal | None = None            # in valuation currency
    stake_value_chf: Decimal | None = None
    moic: Decimal | None = None
    irr: Decimal | None = None
    flows: list = field(default_factory=list)
    missing_fx: bool = False
    runway: Runway | None = None
    last_report: date | None = None
    last_assembly: date | None = None
    next_assembly: date | None = None

    @property
    def currency(self):
        return self.valuation.currency if self.valuation else "CHF"

    @property
    def warnings(self):
        return [c for c in self.classes if c.has_warning]


def _price_per_common_share(valuation):
    if valuation.price_per_share is None:
        return None
    ratio = valuation.share_class.conversion_ratio if valuation.share_class_id else Decimal(1)
    return valuation.price_per_share / ratio


def company_summary(company, as_of=None):
    as_of = as_of or timezone.localdate()
    s = CompanySummary(company=company, as_of=as_of)
    s.ownership = ownership_company(company.pk, as_of)
    s.classes = ownership_by_class(company.pk, as_of)

    # Money in and out, in CHF.
    invested = Decimal(0)
    for inv in company.investments.filter(invest_date__lte=as_of):
        chf = to_chf(inv.amount, inv.currency, inv.invest_date, inv.fx_to_chf)
        if chf is None:
            s.missing_fx = True
            continue
        invested += chf
        if chf:
            s.flows.append((inv.invest_date, -chf))
    for real in company.realizations.filter(realized_on__lte=as_of):
        chf = to_chf(real.proceeds, real.currency, real.realized_on, real.fx_to_chf)
        if chf is None:
            s.missing_fx = True
            continue
        s.realized_chf += chf
        s.flows.append((real.realized_on, chf))
    s.invested_chf = invested

    # Value of our stake from the latest valuation.
    s.valuation = (company.valuations.filter(valuation_date__lte=as_of)
                   .select_related("share_class").order_by("-valuation_date", "-id").first())
    v, own = s.valuation, s.ownership
    if v is not None:
        if v.method == Valuation.Method.WRITE_OFF:
            s.company_value, s.stake_value = Decimal(0), Decimal(0)
        else:
            pps = _price_per_common_share(v)
            if pps is not None and own is not None:
                s.stake_value = own.my_as_converted * pps
                s.company_value = v.post_money or own.fully_diluted_total * pps
            elif v.post_money is not None:
                s.company_value = v.post_money
                if own is not None and own.pct_fully_diluted is not None:
                    s.stake_value = v.post_money * own.pct_fully_diluted / 100
        if s.stake_value is not None:
            s.stake_value_chf = to_chf(s.stake_value, v.currency, as_of)
            if s.stake_value_chf is None:
                s.missing_fx = True

    if s.invested_chf and s.stake_value_chf is not None:
        s.moic = ((s.stake_value_chf + s.realized_chf) / s.invested_chf).quantize(Decimal("0.01"))
        s.irr = xirr(s.flows + [(as_of, s.stake_value_chf)])

    s.runway = runway(company, as_of)

    # Reporting and general assemblies.
    reports = [
        company.interactions.filter(kind="report", occurred_on__lte=as_of)
        .order_by("-occurred_on").values_list("occurred_on", flat=True).first(),
        company.documents.filter(category__in=["investor_report", "financials"], document_date__lte=as_of)
        .order_by("-document_date").values_list("document_date", flat=True).first(),
    ]
    s.last_report = max([d for d in reports if d], default=None)
    assemblies = company.interactions.filter(kind="general_assembly")
    s.last_assembly = (assemblies.filter(occurred_on__lte=as_of).order_by("-occurred_on")
                       .values_list("occurred_on", flat=True).first())
    s.next_assembly = (assemblies.filter(occurred_on__gt=as_of).order_by("occurred_on")
                       .values_list("occurred_on", flat=True).first())
    return s


@dataclass
class PortfolioTotals:
    invested_chf: Decimal = Decimal(0)
    value_chf: Decimal = Decimal(0)
    realized_chf: Decimal = Decimal(0)
    moic: Decimal | None = None
    irr: Decimal | None = None
    incomplete: bool = False


def portfolio_totals(summaries, as_of=None):
    as_of = as_of or timezone.localdate()
    t = PortfolioTotals()
    flows = []
    for s in summaries:
        t.invested_chf += s.invested_chf or 0
        t.realized_chf += s.realized_chf or 0
        if s.stake_value_chf is None and s.invested_chf:
            t.incomplete = True
        t.value_chf += s.stake_value_chf or 0
        t.incomplete = t.incomplete or s.missing_fx
        flows += s.flows
    if t.invested_chf:
        t.moic = ((t.value_chf + t.realized_chf) / t.invested_chf).quantize(Decimal("0.01"))
        t.irr = xirr(flows + [(as_of, t.value_chf)])
    return t


# --- Deal (prospect phase) -------------------------------------------------------

@dataclass
class DealSummary:
    deal: object
    post_money: Decimal | None = None
    implied_stake_pct: Decimal | None = None
    committed: Decimal = Decimal(0)
    committed_pct: Decimal | None = None
    lead: object = None
    participants: list = field(default_factory=list)
    strategics: list = field(default_factory=list)


def deal_summary(deal):
    d = DealSummary(deal=deal)
    if deal.pre_money is not None and deal.round_size is not None:
        d.post_money = deal.pre_money + deal.round_size
    if d.post_money and deal.proposed_ticket:
        d.implied_stake_pct = (100 * deal.proposed_ticket / d.post_money).quantize(Decimal("0.01"))
    d.participants = list(deal.participants.select_related("co_investor", "contact"))
    firm = [p for p in d.participants if p.status in (RoundParticipant.Status.COMMITTED,
                                                      RoundParticipant.Status.SIGNED)]
    d.committed = sum((p.amount or 0 for p in firm if p.currency == deal.currency), Decimal(0))
    if deal.round_size:
        d.committed_pct = (100 * d.committed / deal.round_size).quantize(Decimal("1"))
    d.lead = next((p for p in d.participants if p.role == RoundParticipant.Role.LEAD), None)
    d.strategics = [p for p in d.participants if p.co_investor.kind == "strategic"]
    return d


def timeline(company=None, deal=None, limit=30):
    """Events of a company or deal, newest first: interactions, milestones, valuations, share events."""
    items = []
    interactions = Interaction.objects.select_related("contact")
    if company is not None:
        interactions = interactions.filter(Q(company=company) | Q(deal__company=company))
    else:
        interactions = interactions.filter(Q(deal=deal) | Q(company=deal.company, deal__isnull=True))
    for i in interactions.order_by("-occurred_on", "-id")[:limit]:
        items.append({"date": i.occurred_on, "kind": i.get_kind_display(), "code": i.kind,
                      "title": i.summary, "detail": i.details, "obj": i})
    if company is not None:
        for m in company.milestones.all():
            when = m.achieved_on or m.target_date
            if when:
                items.append({"date": when, "kind": _("Milestone"), "code": "milestone",
                              "title": f"{m.title} ({m.get_status_display()})", "obj": m})
        for v in company.valuations.all():
            items.append({"date": v.valuation_date, "kind": _("Valuation"), "code": "valuation",
                          "title": v.get_method_display(), "obj": v})
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:limit]


def report_documents(company, limit=10):
    return company.documents.filter(category__in=REPORT_DOCUMENT_CATEGORIES).order_by(
        "-document_date", "-uploaded_at")[:limit]



# --- Known investors -----------------------------------------------------------------

@dataclass
class KnownInvestor:
    investor: object
    rounds: list = field(default_factory=list)        # [(FundingRound, is_lead)]
    participations: list = field(default_factory=list)  # RoundParticipant of the company's deals

    @property
    def was_lead(self):
        return any(lead for _r, lead in self.rounds)


def known_investors(company):
    """Investors known in a company: from its funding history and from its deals."""
    from core.models import FundingRoundInvestor

    by_id = {}
    for fri in (FundingRoundInvestor.objects.filter(funding_round__company=company)
                .select_related("co_investor", "funding_round")
                .order_by("funding_round__announced_on", "funding_round__id")):
        entry = by_id.setdefault(fri.co_investor_id, KnownInvestor(fri.co_investor))
        entry.rounds.append((fri.funding_round, fri.is_lead))
    for p in RoundParticipant.objects.filter(deal__company=company).select_related("co_investor", "deal"):
        by_id.setdefault(p.co_investor_id, KnownInvestor(p.co_investor)).participations.append(p)
    return sorted(by_id.values(), key=lambda k: (not k.was_lead, -len(k.rounds), k.investor.name.lower()))
