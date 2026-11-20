"""Bring a company's cap table to the state described in its financing documents.

Claude reads the documents of a closing (certificate of incorporation, disclosure schedule, schedule of
purchasers) and returns the capitalization right after it: classes with their terms and shares outstanding,
the option pool, warrants, the round, and what our entities hold. This applies that state in one go, instead
of re-entering the round step by step: only the difference from what the app already knows is recorded.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from core.models import (FundingRound, FundingRoundInvestor, InvestingEntity, Investment, OptionPool, ShareClass,
                         ShareEvent, Valuation)
from core.services.ownership import ownership_by_class, ownership_company

SUFFIXES = r"\b(sa|ag|gmbh|inc|llc|ltd|limited|corp|corporation|lp|l p|holding|holdings|co|company|sarl|bv)\b"


class SnapshotError(Exception):
    """The documents disagree with what is recorded in a way that needs a person to look."""


@dataclass
class SnapshotResult:
    lines: list = field(default_factory=list)


def _key(name):
    return re.sub(r"[^a-z0-9]+", "", re.sub(SUFFIXES, "", (name or "").lower()))


def _money(value):
    return None if value in (None, "") else Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _price(value):
    return None if value in (None, "") else Decimal(str(value)).quantize(Decimal("0.000001"))


def match_entity(name, entities):
    """The investing entity a purchaser name stands for (same name, ignoring legal forms), or None."""
    key = _key(name)
    if not key:
        return None
    for entity in entities:
        other = _key(entity.name)
        if other and (key == other or (min(len(key), len(other)) >= 6 and (key in other or other in key))):
            return entity
    return None


@transaction.atomic
def apply_snapshot(company, snap, closing_default=None, source_document=None):
    """Create what is missing so that the cap table at the closing date matches the documents."""
    from core.ai.apply import SuggestionError, get_or_create_investor, parse_date

    result = SnapshotResult()
    closing = parse_date(snap.get("closing_date")) if snap.get("closing_date") else (closing_default
                                                                                       or timezone.localdate())
    currency = (snap.get("currency") or "USD").upper()
    classes = snap.get("classes") or []
    if not classes:
        raise SuggestionError("The documents list no share classes.")

    # --- Classes and their terms -------------------------------------------------------------
    ranks = [c["seniority_rank"] for c in classes if not c.get("is_common") and c.get("seniority_rank")]
    lowest = max(ranks) if ranks else 0
    by_name = {}
    for c in classes:
        name = c["name"].strip()
        cls = company.share_classes.filter(name__iexact=name).first()
        seniority = (lowest - c["seniority_rank"] + 1) if c.get("seniority_rank") and not c.get("is_common") else 0
        if cls is None:
            cls = ShareClass.objects.create(
                company=company, name=name, is_common=bool(c.get("is_common")), seniority=seniority,
                original_issue_price=_price(c.get("original_issue_price")), currency=currency,
                liquidation_pref_mult=Decimal(str(c.get("liquidation_multiple") or 1)),
                participating=bool(c.get("participating")),
                conversion_ratio=Decimal(str(c.get("conversion_ratio") or 1)))
            result.lines.append(f"Class {name} created")
        elif cls.original_issue_price is None and c.get("original_issue_price"):
            cls.original_issue_price = _price(c["original_issue_price"])
            cls.save(update_fields=["original_issue_price"])
        by_name[name.lower()] = (cls, c)

    # --- Shares outstanding: record only the difference ---------------------------------------------
    current = {row.share_class.lower(): int(row.shares_outstanding) for row in ownership_by_class(company.pk, closing)}
    for key, (cls, c) in by_name.items():
        delta = int(c["shares_outstanding"]) - current.get(key, 0)
        if delta < 0:
            raise SuggestionError(
                f"{cls.name}: the documents show {c['shares_outstanding']:,} shares outstanding but the app already "
                f"has {current[key]:,}. Correct the share events in Data first, then accept again.")
        if delta > 0:
            ShareEvent.objects.create(
                company=company, share_class=cls, event_type=ShareEvent.Type.ISSUANCE, event_date=closing,
                shares_delta=delta, price_per_share=cls.original_issue_price if not cls.is_common else None,
                source_document=source_document,
                notes=f"{snap.get('round_name') or 'Capitalization'}: from the financing documents")
            result.lines.append(f"{cls.name}: {delta:,} shares added")

    # --- Option pool and warrants ---------------------------------------------------------------
    if snap.get("option_pool_total") is not None or snap.get("warrants") is not None:
        pool, _created = OptionPool.objects.get_or_create(company=company, as_of=closing,
                                                          defaults={"authorized": snap.get("option_pool_total") or 0})
        pool.authorized = snap.get("option_pool_total") or pool.authorized
        pool.granted = snap.get("option_pool_granted") if snap.get("option_pool_granted") is not None else pool.granted
        if pool.granted is not None and pool.granted > pool.authorized:
            pool.granted = None
        pool.warrants = snap.get("warrants") or 0
        pool.save()
        result.lines.append(f"Option pool {pool.authorized:,}, warrants {pool.warrants:,}")

    totals = ownership_company(company.pk, closing)
    fully_diluted = totals.fully_diluted_total if totals else None
    if snap.get("fully_diluted_total") and fully_diluted is not None and int(fully_diluted) != snap["fully_diluted_total"]:
        raise SuggestionError(
            f"After applying, the fully diluted total is {int(fully_diluted):,} but the documents state "
            f"{snap['fully_diluted_total']:,}. Check the existing share events and option pool in Data, "
            "then accept again.")

    # --- The round: funding history and valuation -------------------------------------------------
    round_cls = by_name.get((snap.get("round_class_name") or "").lower(), (None, None))[0]
    price = _price(snap.get("round_price_per_share")) or (round_cls.original_issue_price if round_cls else None)
    round_name = (snap.get("round_name") or "").strip()
    amount = _money(snap.get("round_amount_raised"))
    if amount is None and price and round_cls:
        shares = int(by_name[round_cls.name.lower()][1]["shares_outstanding"])
        amount = _money(price * shares)
    post = _money(snap.get("post_money"))
    if post is None and price and fully_diluted:
        ratio = round_cls.conversion_ratio if round_cls else Decimal(1)
        post = _money(price / ratio * Decimal(int(fully_diluted)))
    pre = _money(snap.get("pre_money"))
    if pre is None and post is not None and amount is not None:
        pre = post - amount

    if round_name:
        funding_round = company.funding_rounds.filter(round_name__iexact=round_name,
                                                      announced_on__year=closing.year).first() \
            or FundingRound(company=company, round_name=round_name)
        funding_round.announced_on = closing
        funding_round.currency = currency
        for attr, value in (("amount", amount), ("pre_money", pre), ("post_money", post)):
            if value is not None:
                setattr(funding_round, attr, value)
        funding_round.save()
        for investor in snap.get("lead_investors") or []:
            FundingRoundInvestor.objects.update_or_create(
                funding_round=funding_round, co_investor=get_or_create_investor(investor["name"], investor["kind"]),
                defaults={"is_lead": True})
        for investor in snap.get("other_investors") or []:
            FundingRoundInvestor.objects.get_or_create(
                funding_round=funding_round, co_investor=get_or_create_investor(investor["name"], investor["kind"]))
        result.lines.append(f"{round_name} in the funding history")

    if post is not None or price:
        valuation = (Valuation.objects.filter(company=company, valuation_date=closing,
                                              method=Valuation.Method.ROUND).first()
                     or Valuation(company=company, valuation_date=closing, method=Valuation.Method.ROUND,
                                  currency=currency))
        valuation.price_per_share = price or valuation.price_per_share
        valuation.share_class = round_cls if price and round_cls else valuation.share_class
        valuation.post_money = post if post is not None else valuation.post_money
        valuation.notes = valuation.notes or round_name or None
        valuation.save()
        if post is not None:
            result.lines.append(f"Post-money {currency} {post:,.0f}")

    # --- What our entities hold --------------------------------------------------------------------
    entities = list(InvestingEntity.objects.all())
    for h in snap.get("our_holdings") or []:
        cls = by_name.get(h["class_name"].strip().lower(), (None, None))[0] \
            or company.share_classes.filter(name__iexact=h["class_name"].strip()).first()
        entity = match_entity(h["investor_name"], entities) or (entities[0] if len(entities) == 1 else None)
        if cls is None or entity is None:
            raise SuggestionError(
                f"Cannot place the holding of {h['investor_name']}: "
                + ("share class not found." if cls is None else "no investing entity with that name. Add it in Data "
                   "› Investing entities first, then accept again."))
        if Investment.objects.filter(company=company, share_class=cls, entity=entity, shares=h["shares"]).exists():
            continue
        unit = _price(h.get("price_per_share")) or cls.original_issue_price
        paid = _money(h.get("amount")) or (_money(unit * h["shares"]) if unit else None)
        Investment.objects.create(
            company=company, entity=entity, share_class=cls, invest_date=closing, instrument="equity",
            shares=h["shares"], price_per_share=unit, amount=paid or Decimal(0), currency=currency,
            source_document=source_document, notes=f"{round_name or 'Investment'} (from the financing documents)")
        result.lines.append(f"Investment {entity.name}: {h['shares']:,} {cls.name}")
        if company.status == company.Status.PROSPECT:
            company.status = company.Status.PORTFOLIO
            company.save(update_fields=["status", "updated_at"])
    return result
