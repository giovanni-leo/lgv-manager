"""Conversion of amounts to CHF."""

from decimal import Decimal

from django.conf import settings

from core.models import FxRate


def rate_to_chf(currency, on_date):
    """CHF per unit of ``currency`` on ``on_date`` (latest rate on or before it), or None."""
    if not currency or currency == settings.BASE_CURRENCY:
        return Decimal(1)
    rate = (FxRate.objects.filter(currency=currency, rate_date__lte=on_date)
            .order_by("-rate_date").values_list("chf_per_unit", flat=True).first())
    return rate


def convert(amount, from_currency, to_currency, on_date, override_chf=None):
    """Amount in another currency, through CHF (the rates are CHF per unit). None when a rate is missing.

    ``override_chf`` is the CHF rate recorded on the transaction itself: it replaces the table for the
    ``from_currency`` -> CHF step.
    """
    if amount is None:
        return None
    if from_currency == to_currency:
        return Decimal(amount)
    in_chf = to_chf(amount, from_currency, on_date, override_chf)
    if in_chf is None or to_currency == settings.BASE_CURRENCY:
        return in_chf
    rate = rate_to_chf(to_currency, on_date)
    return None if not rate else in_chf / Decimal(rate)


def to_chf(amount, currency, on_date, override=None):
    """Amount in CHF. ``override`` is a rate recorded on the transaction itself."""
    if amount is None:
        return None
    rate = override or rate_to_chf(currency, on_date)
    return None if rate is None else Decimal(amount) * Decimal(rate)
