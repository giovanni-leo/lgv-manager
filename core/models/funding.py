"""Financing history of a company: past rounds and who invested in them."""

from django.db import models
from django.db.models.functions import Now
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .common import currency_check, currency_field, money_field
from .parties import CoInvestor, Company


class FundingRound(models.Model):
    """A financing round of a company, ours or not (e.g. found by web research)."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="funding_rounds",
                                verbose_name=_("company"))
    round_name = models.TextField(_("round"))
    announced_on = models.DateField(_("date"), null=True, blank=True)
    amount = money_field(_("amount raised"))
    pre_money = money_field(_("pre-money valuation"))
    post_money = money_field(_("post-money valuation"))
    currency = currency_field()
    source_url = models.TextField(_("source"), null=True, blank=True)
    notes = models.TextField(_("notes"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), default=timezone.now, db_default=Now(), editable=False)

    class Meta:
        db_table = "funding_rounds"
        ordering = ["company", "-announced_on", "-id"]
        verbose_name = _("funding round")
        verbose_name_plural = _("funding rounds")
        constraints = [currency_check("funding_rounds_currency_check")]

    def __str__(self):
        when = f" ({self.announced_on:%Y-%m})" if self.announced_on else ""
        return f"{self.company} – {self.round_name}{when}"

    @property
    def leads(self):
        return [i.co_investor for i in self.investors.all() if i.is_lead]

    @property
    def others(self):
        return [i.co_investor for i in self.investors.all() if not i.is_lead]


class FundingRoundInvestor(models.Model):
    funding_round = models.ForeignKey(FundingRound, on_delete=models.CASCADE, related_name="investors",
                                      verbose_name=_("funding round"))
    co_investor = models.ForeignKey(CoInvestor, on_delete=models.PROTECT, related_name="funding_rounds",
                                    verbose_name=_("investor"))
    is_lead = models.BooleanField(_("lead"), default=False, db_default=False)

    class Meta:
        db_table = "funding_round_investors"
        ordering = ["-is_lead", "co_investor__name"]
        verbose_name = _("investor in a round")
        verbose_name_plural = _("investors in the round")
        constraints = [
            models.UniqueConstraint(fields=["funding_round", "co_investor"], name="funding_round_investors_key"),
        ]

    def __str__(self):
        return f"{self.co_investor}{' (lead)' if self.is_lead else ''}"
