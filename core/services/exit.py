"""What each class, and we, receive if the company is sold for a given amount.

Uses the terms stored on each share class: liquidation preference multiple, participating or not,
seniority (higher is paid first, equal is pari passu) and conversion ratio. Non-participating preferred
shares take the better of their preference and converting to common. Options and warrants count as
common shares, without deducting their exercise price (slightly prudent for the preferred holders).
"""

from dataclasses import dataclass, field
from decimal import Decimal

from core.services.ownership import ownership_by_class, ownership_company


@dataclass
class ClassTerms:
    name: str
    shares: Decimal
    ratio: Decimal
    preference: Decimal          # total amount owed before common (0 for common)
    seniority: int
    participating: bool
    is_common: bool
    mine: Decimal = Decimal(0)


@dataclass
class ClassPayout:
    name: str
    shares: Decimal
    preference: Decimal
    payout: Decimal
    converted: bool
    mine: Decimal
    my_payout: Decimal

    @property
    def per_share(self):
        return self.payout / self.shares if self.shares else Decimal(0)


@dataclass
class ExitResult:
    exit_value: Decimal
    rows: list
    per_common_share: Decimal
    my_payout: Decimal = Decimal(0)


@dataclass
class ExitModel:
    classes: list
    other_common: Decimal                       # options and warrants, as common
    warnings: list = field(default_factory=list)


def build_model(company, as_of=None):
    """Terms and positions of a company today (or at ``as_of``)."""
    rows = {r.share_class_id: r for r in ownership_by_class(company.pk, as_of)}
    totals = ownership_company(company.pk, as_of)
    classes, warnings = [], []
    for cls in company.share_classes.all():
        row = rows.get(cls.pk)
        shares = Decimal(row.shares_outstanding) if row else Decimal(0)
        if shares <= 0:
            continue
        preference = Decimal(0)
        if not cls.is_common:
            if cls.original_issue_price is None:
                warnings.append(f"{cls.name} has no original issue price: its preference counts as zero.")
            else:
                preference = shares * cls.original_issue_price * cls.liquidation_pref_mult
        classes.append(ClassTerms(cls.name, shares, cls.conversion_ratio, preference, cls.seniority,
                                  cls.participating, cls.is_common, Decimal(row.my_shares) if row else Decimal(0)))
    other = Decimal(totals.option_pool + totals.warrants) if totals else Decimal(0)
    return ExitModel(classes, other, warnings)


def _distribute(model, exit_value, converted):
    """Payouts for one set of conversion decisions. Returns ({name: payout}, per common share)."""
    remaining = Decimal(exit_value)
    pay = {c.name: Decimal(0) for c in model.classes}
    prefs = [c for c in model.classes if c.preference and c.name not in converted]
    for seniority in sorted({c.seniority for c in prefs}, reverse=True):
        tier = [c for c in prefs if c.seniority == seniority]
        owed = sum(c.preference for c in tier)
        paid = min(remaining, owed)
        for c in tier:
            pay[c.name] += paid * c.preference / owed
        remaining -= paid
    sharing = model.other_common
    for c in model.classes:
        if c.is_common or c.name in converted or (c.participating and c.preference):
            sharing += c.shares * c.ratio
    per_share = remaining / sharing if sharing else Decimal(0)
    for c in model.classes:
        if c.is_common or c.name in converted or (c.participating and c.preference):
            pay[c.name] += per_share * c.shares * c.ratio
    return pay, per_share


def exit_payout(model, exit_value):
    """Waterfall for an exit (sale of the whole company for ``exit_value`` net to shareholders)."""
    exit_value = Decimal(exit_value)
    converted = set()
    while True:
        pay, per_share = _distribute(model, exit_value, converted)
        gains = []
        for c in model.classes:
            if c.is_common or c.participating or c.name in converted or not c.preference:
                continue
            as_common = per_share * c.shares * c.ratio
            if as_common > pay[c.name]:
                gains.append((as_common / c.shares - pay[c.name] / c.shares, c.name))
        if not gains:
            break
        converted.add(max(gains)[1])
        # Re-run with the new decision; a class that converted never converts back (its threshold is passed).
    rows, mine_total = [], Decimal(0)
    for c in sorted(model.classes, key=lambda x: (x.is_common, -x.seniority, x.name)):
        share = pay[c.name]
        mine = share * c.mine / c.shares if c.shares else Decimal(0)
        mine_total += mine
        rows.append(ClassPayout(c.name, c.shares, c.preference, share, c.name in converted, c.mine, mine))
    if model.other_common:
        rows.append(ClassPayout("Options and warrants", model.other_common, Decimal(0),
                                per_share * model.other_common, False, Decimal(0), Decimal(0)))
    return ExitResult(exit_value, rows, per_share, mine_total)


def breakeven(model, invested, high=None):
    """Smallest exit value where our payout reaches ``invested`` (None if it never does within ``high``)."""
    invested = Decimal(invested) - 1      # rounding of prices and amounts must not matter
    if invested <= 0:
        return None
    high = Decimal(high or invested * 1000)
    if exit_payout(model, high).my_payout < invested:
        return None
    low = Decimal(0)
    for _ in range(60):
        mid = (low + high) / 2
        if exit_payout(model, mid).my_payout >= invested:
            high = mid
        else:
            low = mid
    return high.quantize(Decimal("1"))


def parse_exit_value(text):
    """'100M', '1.5bn', "120'000'000", '250k' -> Decimal, or None when it is not a positive number."""
    import re

    match = re.fullmatch(r"\s*([0-9][0-9'’ ,]*(?:\.[0-9]+)?)\s*(k|m|mm|bn|b)?\s*", (text or "").lower())
    if not match:
        return None
    number = Decimal(re.sub(r"['’ ,]", "", match.group(1)))
    factor = {None: 1, "k": 1_000, "m": 1_000_000, "mm": 1_000_000, "b": 1_000_000_000, "bn": 1_000_000_000}[match.group(2)]
    value = number * factor
    return value if 0 < value < Decimal("1e13") else None


SCENARIO_FACTORS = [Decimal("0.25"), Decimal("0.5"), Decimal("1"), Decimal("2"), Decimal("4")]


def exit_analysis(company, exit_text=None):
    """Everything the Exit card shows, or None when the company has no cap table with our shares."""
    from core.models import Investment

    model = build_model(company)
    if not any(c.mine for c in model.classes):
        return None
    valuation = company.valuations.exclude(post_money=None).order_by("-valuation_date", "-id").first()
    currency = valuation.currency if valuation else (company.share_classes.exclude(currency=None).values_list(
        "currency", flat=True).first() or "CHF")
    base = valuation.post_money if valuation else None
    chosen = parse_exit_value(exit_text) if exit_text else None
    invested = sum((i.amount for i in Investment.objects.filter(company=company, currency=currency)), Decimal(0))

    def multiple(payout):
        return payout / invested if invested else None

    detail = exit_payout(model, chosen or base) if (chosen or base) else None
    scenarios = []
    if base:
        for factor in SCENARIO_FACTORS:
            result = exit_payout(model, base * factor)
            scenarios.append({"factor": factor, "exit_value": result.exit_value, "payout": result.my_payout,
                              "multiple": multiple(result.my_payout)})
    return {
        "currency": currency, "base": base, "base_date": valuation.valuation_date if valuation else None,
        "chosen": chosen, "bad_input": bool(exit_text and not chosen), "detail": detail,
        "detail_multiple": multiple(detail.my_payout) if detail else None,
        "scenarios": scenarios, "invested": invested, "warnings": model.warnings,
        "breakeven": breakeven(model, invested) if invested else None,
        "default_text": exit_text or "",
    }
