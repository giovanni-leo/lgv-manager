"""Small helpers to build cap tables in tests."""

from datetime import date
from decimal import Decimal

from core.models import Company, InvestingEntity, Investment, OptionPool, Realization, ShareClass, ShareEvent


def entity(name="Lake Growth Ventures", kind="fund"):
    return InvestingEntity.objects.create(name=name, kind=kind)


def company(name="MedCo SA"):
    return Company.objects.create(name=name, status="portfolio")


def share_class(company, name, ratio="1", common=False, seniority=0):
    return ShareClass.objects.create(company=company, name=name, conversion_ratio=Decimal(ratio),
                                     is_common=common, seniority=seniority)


def issue(share_class, shares, on, event_type="issuance"):
    return ShareEvent.objects.create(company=share_class.company, share_class=share_class,
                                     event_date=on, event_type=event_type, shares_delta=shares)


def split(share_class, ratio, on):
    return ShareEvent.objects.create(company=share_class.company, share_class=share_class,
                                     event_date=on, event_type="split", split_ratio=Decimal(ratio))


def buy(entity, share_class, shares, on, amount="0"):
    return Investment.objects.create(company=share_class.company, entity=entity, share_class=share_class,
                                     invest_date=on, instrument="equity", shares=shares,
                                     amount=Decimal(amount))


def sell(entity, share_class, shares, on, proceeds="0"):
    return Realization.objects.create(company=share_class.company, entity=entity, share_class=share_class,
                                      realized_on=on, kind="sale", shares_sold=shares,
                                      proceeds=Decimal(proceeds))


def pool(company, authorized, on, warrants=0, granted=None):
    return OptionPool.objects.create(company=company, as_of=on, authorized=authorized,
                                     warrants=warrants, granted=granted)


D = date
