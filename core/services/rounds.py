"""Record a financing round in one step.

From a few figures (class, price, shares issued, our participation, option
pool) this creates every record the cap table and the portfolio figures need:
share classes and issuance events, our investment, the option pool, a
valuation and the funding-history entry, and marks the deal as invested.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from core.models import (Company, DealStage, FundingRound, FundingRoundInvestor, Investment, OptionPool,
                         ShareClass, ShareEvent, Valuation)

from .ownership import ownership_company


@dataclass
class ExistingClass:
    """Shares already outstanding before the round (first time a cap table is entered)."""

    name: str
    shares: int
    is_common: bool = False


@dataclass
class RoundInput:
    round_name: str
    round_date: date
    new_shares: int
    share_class: ShareClass | None = None      # an existing class of the company...
    new_class_name: str = ""                   # ...or the name of a new one
    price_per_share: Decimal | None = None
    currency: str = "CHF"
    pre_money: Decimal | None = None
    our_shares: int | None = None
    our_amount: Decimal | None = None
    entity: object = None
    pool_size: int | None = None
    warrants: int | None = None
    lead_investors: list = field(default_factory=list)
    other_investors: list = field(default_factory=list)
    existing: list = field(default_factory=list)  # [ExistingClass]
    deal: object = None
    notes: str = ""


@dataclass
class RoundResult:
    share_class: ShareClass
    funding_round: FundingRound
    investment: Investment | None
    valuation: Valuation | None
    amount_raised: Decimal | None
    pre_money: Decimal | None
    post_money: Decimal | None
    created_classes: list


def _money(value):
    return None if value is None else Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate(company, data):
    errors = {}
    if data.share_class is None and not data.new_class_name.strip():
        errors["share_class"] = "Choose the share class of the round or type the name of a new one."
    if data.share_class is not None and data.share_class.company_id != company.pk:
        errors["share_class"] = "This share class belongs to another company."
    if data.new_class_name.strip() and company.share_classes.filter(name__iexact=data.new_class_name.strip()).exists():
        errors["new_class_name"] = "A class with this name already exists: choose it from the list."
    if not data.new_shares or data.new_shares <= 0:
        errors["new_shares"] = "Enter the number of new shares issued in the round."
    if data.our_shares and data.new_shares and data.our_shares > data.new_shares:
        errors["our_shares"] = "Our shares cannot exceed the shares issued in the round."
    if data.our_amount and not data.our_shares and not data.price_per_share:
        errors["our_shares"] = "Enter our number of shares, or the price per share to work it out."
    if (data.our_shares or data.our_amount) and data.entity is None:
        errors["entity"] = "Choose which investing entity made the investment."
    if data.pool_size is not None and data.pool_size < 0:
        errors["pool_size"] = "The option pool cannot be negative."
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def record_round(company: Company, data: RoundInput) -> RoundResult:
    validate(company, data)
    created = []
    price = data.price_per_share
    seniority = (company.share_classes.aggregate(m=Max("seniority"))["m"] or 0)

    # Cap table before the round, when it is entered for the first time.
    for row in data.existing:
        if not row.name.strip() or not row.shares:
            continue
        cls, made = ShareClass.objects.get_or_create(
            company=company, name=row.name.strip(),
            defaults={"is_common": row.is_common, "currency": data.currency})
        if made:
            created.append(cls)
        ShareEvent.objects.create(company=company, share_class=cls, event_type=ShareEvent.Type.ISSUANCE,
                                  event_date=data.round_date - timedelta(days=1), shares_delta=row.shares,
                                  notes="Shares outstanding before the round")

    # The class of the round and the new shares.
    cls = data.share_class
    if cls is None:
        cls = ShareClass.objects.create(company=company, name=data.new_class_name.strip(), seniority=seniority + 1,
                                        original_issue_price=price, currency=data.currency)
        created.append(cls)
    ShareEvent.objects.create(company=company, share_class=cls, event_type=ShareEvent.Type.ISSUANCE,
                              event_date=data.round_date, shares_delta=data.new_shares, price_per_share=price,
                              notes=data.round_name)

    # Our participation.
    investment = None
    our_shares, our_amount = data.our_shares, _money(data.our_amount)
    if our_amount and not our_shares and price:
        our_shares = int((our_amount / price).to_integral_value(rounding=ROUND_HALF_UP))
    if our_shares and our_amount is None and price:
        our_amount = _money(price * our_shares)
    if our_shares:
        investment = Investment.objects.create(
            company=company, entity=data.entity, deal=data.deal, share_class=cls, invest_date=data.round_date,
            instrument=Investment.Instrument.EQUITY, shares=our_shares, price_per_share=price,
            amount=our_amount or Decimal(0), currency=data.currency, notes=data.round_name)

    # Option pool after the round.
    if data.pool_size is not None or data.warrants is not None:
        OptionPool.objects.update_or_create(
            company=company, as_of=data.round_date,
            defaults={"authorized": data.pool_size or 0, "warrants": data.warrants or 0})

    # Valuation: given pre-money, or price x fully diluted shares after the round.
    amount_raised = _money(price * data.new_shares) if price else None
    pre, post = _money(data.pre_money), None
    if pre is not None and amount_raised is not None:
        post = pre + amount_raised
    elif price:
        totals = ownership_company(company.pk, data.round_date)
        if totals:
            post = _money(price / cls.conversion_ratio * totals.fully_diluted_total)
            pre = post - amount_raised if amount_raised is not None else None
    valuation = None
    if price or post:
        valuation = Valuation.objects.create(
            company=company, valuation_date=data.round_date, method=Valuation.Method.ROUND,
            price_per_share=price, share_class=cls if price else None, post_money=post, currency=data.currency,
            notes=data.round_name)

    # Funding history (completes a row found earlier by web research, if any).
    funding_round = (company.funding_rounds.filter(round_name__iexact=data.round_name.strip(),
                                                   announced_on__year=data.round_date.year).first()
                     or FundingRound(company=company, round_name=data.round_name.strip()))
    funding_round.announced_on = data.round_date
    funding_round.amount = amount_raised if amount_raised is not None else funding_round.amount
    funding_round.pre_money = pre if pre is not None else funding_round.pre_money
    funding_round.post_money = post if post is not None else funding_round.post_money
    funding_round.currency = data.currency
    if data.notes:
        funding_round.notes = data.notes
    funding_round.save()
    from core.ai.apply import get_or_create_investor

    for name in data.lead_investors:
        FundingRoundInvestor.objects.update_or_create(
            funding_round=funding_round, co_investor=get_or_create_investor(name, "vc"), defaults={"is_lead": True})
    for name in data.other_investors:
        FundingRoundInvestor.objects.get_or_create(funding_round=funding_round,
                                                   co_investor=get_or_create_investor(name, "vc"))

    # We invested: the deal closes and the company joins the portfolio.
    if investment is not None:
        if data.deal is not None and data.deal.stage != DealStage.INVESTED:
            data.deal.stage = DealStage.INVESTED
            data.deal.closed_on = data.deal.closed_on or data.round_date
            data.deal.save()
        if company.status == Company.Status.PROSPECT:
            company.status = Company.Status.PORTFOLIO
            company.save(update_fields=["status", "updated_at"])

    return RoundResult(share_class=cls, funding_round=funding_round, investment=investment, valuation=valuation,
                       amount_raised=amount_raised, pre_money=pre, post_money=post, created_classes=created)
