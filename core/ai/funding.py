"""Record a financing round (from a suggestion) in the funding history.

Model classes are passed in so the same code works in a data migration
(historical models) and in the application.
"""

import re

from .apply import SuggestionError, parse_amount, parse_date


def _safe(parser, value):
    try:
        return parser(value)
    except SuggestionError:
        return None


def _investor(CoInvestor, name, kind):
    investor = CoInvestor.objects.filter(name__iexact=name.strip()).first()
    if investor is None:
        kinds = {"vc", "strategic", "angel", "family_office", "public", "other"}
        investor = CoInvestor.objects.create(name=name.strip(), kind=kind if kind in kinds else "other")
    return investor


def record_funding_round(company, payload, FundingRound, FundingRoundInvestor, CoInvestor):
    """Create or complete the round described by a past-round payload. Returns it."""
    name = (payload.get("round_name") or "Round").strip()[:200]
    when = _safe(parse_date, payload.get("announced_on"))
    currency = (payload.get("currency") or "CHF").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        currency = "CHF"
    amount = _safe(parse_amount, payload.get("amount"))
    pre = _safe(parse_amount, payload.get("pre_money"))
    post = _safe(parse_amount, payload.get("post_money"))
    if post is None and pre is not None and amount is not None:
        post = pre + amount

    funding_round = FundingRound.objects.filter(company=company, round_name__iexact=name,
                                                announced_on=when).first()
    if funding_round is None:
        funding_round = FundingRound(company=company, round_name=name, announced_on=when, currency=currency)
    for field, value in (("amount", amount), ("pre_money", pre), ("post_money", post),
                         ("source_url", payload.get("source_url"))):
        if value is not None and getattr(funding_round, field) is None:
            setattr(funding_round, field, value)
    funding_round.save()

    for investor in payload.get("lead_investors") or []:
        FundingRoundInvestor.objects.update_or_create(
            funding_round=funding_round, co_investor=_investor(CoInvestor, investor["name"], investor.get("kind")),
            defaults={"is_lead": True})
    for investor in payload.get("other_investors") or []:
        FundingRoundInvestor.objects.get_or_create(
            funding_round=funding_round, co_investor=_investor(CoInvestor, investor["name"], investor.get("kind")))
    return funding_round
