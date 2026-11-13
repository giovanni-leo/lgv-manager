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


def to_chf(amount, currency, on_date, override=None):
    """Amount in CHF. ``override`` is a rate recorded on the transaction itself."""
    if amount is None:
        return None
    rate = override or rate_to_chf(currency, on_date)
    return None if rate is None else Decimal(amount) * Decimal(rate)
