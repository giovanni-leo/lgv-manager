"""Exchange rates from the European Central Bank's daily reference rates (free, official, history since 1999).

The ECB publishes units of each currency per 1 EUR. The app stores CHF per unit (table fx_rates), so
CHF per EUR is the ECB's CHF rate and CHF per USD is (CHF per EUR) / (USD per EUR). Rates already in the
table, for example typed by hand, are never overwritten.
"""

import logging
import urllib.request
from decimal import ROUND_HALF_UP, Decimal
from xml.etree import ElementTree

from django.conf import settings

from core.models import FxRate

log = logging.getLogger(__name__)

URL_LAST_90_DAYS = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
URL_FULL_HISTORY = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
EIGHT = Decimal("0.00000001")


def parse(xml_bytes):
    """[(date, {currency: units per EUR})] from an ECB file."""
    from datetime import date

    out = []
    for node in ElementTree.fromstring(xml_bytes).iter():
        if node.tag.endswith("Cube") and node.get("time"):
            rates = {c.get("currency"): Decimal(c.get("rate")) for c in node if c.get("currency")}
            out.append((date.fromisoformat(node.get("time")), rates))
    return out


def chf_per_unit(rates, currency):
    """CHF per one unit of ``currency`` given the ECB rates of one day, or None."""
    chf_per_eur = rates.get(settings.BASE_CURRENCY)
    if not chf_per_eur:
        return None
    if currency == "EUR":
        return chf_per_eur.quantize(EIGHT, rounding=ROUND_HALF_UP)
    units_per_eur = rates.get(currency)
    return (chf_per_eur / units_per_eur).quantize(EIGHT, rounding=ROUND_HALF_UP) if units_per_eur else None


def store(days):
    """Add the missing rates for the currencies in use. Returns how many were added."""
    wanted = [c for c in settings.CURRENCIES if c != settings.BASE_CURRENCY]
    have = set(FxRate.objects.filter(currency__in=wanted).values_list("rate_date", "currency"))
    new = []
    for day, rates in days:
        for currency in wanted:
            value = chf_per_unit(rates, currency)
            if value and (day, currency) not in have:
                new.append(FxRate(rate_date=day, currency=currency, chf_per_unit=value))
    FxRate.objects.bulk_create(new, ignore_conflicts=True)
    return len(new)


def download(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        return response.read()


def update(full=False, fetch=download):
    """Download the rates and add the missing ones. The 90-day file is enough for daily updates."""
    return store(parse(fetch(URL_FULL_HISTORY if full else URL_LAST_90_DAYS)))
