"""Share classes, cap-table events, option pools, my investments and exits.

Integrity rules that the database enforces beyond plain foreign keys:

* ``share_events``, ``investments``, ``realizations`` and ``valuations`` carry a
  composite foreign key ``(share_class_id, company_id) -> share_classes(id,
  company_id)`` so a share class can never belong to a different company than
  the row that points at it (see migration 0002).
* Signs of ``shares_delta`` are checked per event type.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .common import currency_check, currency_field, money_field, price_field
from .parties import Company, InvestingEntity
from .pipeline import Deal


class ShareClass(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="share_classes",
                                verbose_name=_("company"))
    name = models.TextField(_("name"), help_text=_("e.g. Common, Series A, Series B"))
    is_common = models.BooleanField(_("common shares"), default=False, db_default=False)
    seniority = models.IntegerField(_("seniority"), default=0, db_default=0,
                                    help_text=_("Higher means more senior in liquidation."))
    nominal_value = price_field(_("nominal value"))
    original_issue_price = price_field(_("original issue price"))
    currency = currency_field()
    liquidation_pref_mult = models.DecimalField(
        _("liquidation preference (×)"), max_digits=6, decimal_places=3, default=1, db_default=1)
    participating = models.BooleanField(_("participating"), default=False, db_default=False)
    conversion_ratio = models.DecimalField(
        _("conversion ratio"), max_digits=12, decimal_places=6, default=1, db_default=1,
        help_text=_("Common shares obtained for one share of this class."))
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "share_classes"
        ordering = ["company", "-seniority", "name"]
        verbose_name = _("share class")
        verbose_name_plural = _("share classes")
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="share_classes_company_id_name_key"),
            # Target of the composite foreign keys that tie a class to its company.
            models.UniqueConstraint(fields=["id", "company"], name="share_classes_id_company_id_key"),
            models.CheckConstraint(condition=Q(conversion_ratio__gt=0),
                                   name="share_classes_conversion_ratio_check"),
            models.CheckConstraint(condition=Q(liquidation_pref_mult__gte=0),
                                   name="share_classes_liquidation_pref_check"),
            currency_check("share_classes_currency_check"),
        ]

    def __str__(self):
        return f"{self.company.name} – {self.name}" if self.company_id else self.name


def _check_class_company(obj, errors):
    if obj.share_class_id and obj.company_id and obj.share_class.company_id != obj.company_id:
        errors["share_class"] = _("The share class belongs to another company.")


class ShareEvent(models.Model):
    """A change in the number of shares outstanding of one class.

    A ``split`` event multiplies every earlier position of the class (both the
    class total and my shares) by ``split_ratio``: 2 for a 2-for-1 split, 0.1
    for a 1-for-10 reverse split. Events dated the same day as the split are
    considered to be already post-split. For splits ``shares_delta`` is only
    informative (filled in automatically); the ownership functions derive the
    effect from ``split_ratio``.
    """

    class Type(models.TextChoices):
        ISSUANCE = "issuance", _("Issuance")
        CONVERSION_IN = "conversion_in", _("Conversion in")
        CONVERSION_OUT = "conversion_out", _("Conversion out")
        BUYBACK = "buyback", _("Buyback")
        CANCELLATION = "cancellation", _("Cancellation")
        SPLIT = "split", _("Split / reverse split")

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="share_events",
                                verbose_name=_("company"))
    share_class = models.ForeignKey(ShareClass, on_delete=models.CASCADE, related_name="events",
                                    verbose_name=_("share class"))
    event_date = models.DateField(_("event date"))
    event_type = models.TextField(_("event type"), choices=Type.choices)
    shares_delta = models.BigIntegerField(_("shares delta"), default=0,
                                          help_text=_("Positive or negative. Computed automatically for splits."))
    split_ratio = models.DecimalField(_("split ratio"), max_digits=12, decimal_places=6, null=True, blank=True,
                                      help_text=_("Only for splits: new shares per old share (0.1 = 1-for-10)."))
    price_per_share = price_field(_("price per share"))
    source_document = models.ForeignKey("Document", on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="+", verbose_name=_("source document"))
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "share_events"
        ordering = ["company", "event_date", "id"]
        verbose_name = _("share event")
        verbose_name_plural = _("share events")
        constraints = [
            models.CheckConstraint(
                condition=Q(event_type__in=["issuance", "conversion_in", "conversion_out",
                                            "buyback", "cancellation", "split"]),
                name="share_events_event_type_check",
            ),
            models.CheckConstraint(
                condition=(Q(event_type="split", split_ratio__isnull=False, split_ratio__gt=0)
                           | (~Q(event_type="split") & Q(split_ratio__isnull=True))),
                name="share_events_split_ratio_check",
            ),
            models.CheckConstraint(
                condition=(~Q(event_type__in=["issuance", "conversion_in"]) | Q(shares_delta__gt=0)),
                name="share_events_positive_delta_check",
            ),
            models.CheckConstraint(
                condition=(~Q(event_type__in=["conversion_out", "buyback", "cancellation"])
                           | Q(shares_delta__lt=0)),
                name="share_events_negative_delta_check",
            ),
        ]
        indexes = [models.Index(fields=["share_class", "event_date"], name="share_events_class_date_idx")]

    def __str__(self):
        return f"{self.event_date} {self.get_event_type_display()} {self.shares_delta:+,}"

    def clean(self):
        errors = {}
        _check_class_company(self, errors)
        if self.event_type == self.Type.SPLIT and not self.split_ratio:
            errors["split_ratio"] = _("A split needs a ratio.")
        if self.event_type != self.Type.SPLIT and self.split_ratio is not None:
            errors["split_ratio"] = _("Only splits have a ratio.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.share_class_id and not self.company_id:
            self.company_id = self.share_class.company_id
        if self.event_type == self.Type.SPLIT and self.split_ratio:
            from core.services.ownership import class_outstanding_before

            before = class_outstanding_before(self.share_class_id, self.event_date)
            self.shares_delta = int(round(before * self.split_ratio - before))
        super().save(*args, **kwargs)


class OptionPool(models.Model):
    """Option pool snapshot used for the fully diluted calculation.

    ``authorized`` is the whole pool not yet exercised (granted + ungranted);
    options already exercised are common shares and must be recorded as
    issuance events instead. ``warrants`` counts every outstanding warrant of
    the company, mine included.
    """

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="option_pools",
                                verbose_name=_("company"))
    as_of = models.DateField(_("as of"))
    authorized = models.BigIntegerField(_("pool size (granted + ungranted)"))
    granted = models.BigIntegerField(_("granted"), null=True, blank=True)
    warrants = models.BigIntegerField(_("warrants outstanding"), default=0, db_default=0)
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "option_pools"
        ordering = ["company", "-as_of"]
        verbose_name = _("option pool")
        verbose_name_plural = _("option pools")
        constraints = [
            models.UniqueConstraint(fields=["company", "as_of"], name="option_pools_company_as_of_key"),
            models.CheckConstraint(condition=Q(authorized__gte=0) & Q(warrants__gte=0),
                                   name="option_pools_non_negative_check"),
            models.CheckConstraint(
                condition=Q(granted__isnull=True) | (Q(granted__gte=0) & Q(granted__lte=models.F("authorized"))),
                name="option_pools_granted_check",
            ),
        ]

    def __str__(self):
        return f"{self.company} {self.as_of}: {self.authorized:,}"


class Investment(models.Model):
    class Instrument(models.TextChoices):
        EQUITY = "equity", _("Equity")
        CONVERTIBLE_LOAN = "convertible_loan", _("Convertible loan")
        SAFE = "safe", _("SAFE")
        WARRANT = "warrant", _("Warrant")
        SPV = "spv", _("Interest in an SPV / fund")
        REPORTED = "reported", _("Position at the value reported by the fund")

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="investments",
                                verbose_name=_("company"))
    entity = models.ForeignKey(InvestingEntity, on_delete=models.RESTRICT, related_name="investments",
                               verbose_name=_("investing entity"))
    deal = models.ForeignKey(Deal, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="investments", verbose_name=_("deal"))
    share_class = models.ForeignKey(ShareClass, on_delete=models.RESTRICT, null=True, blank=True,
                                    related_name="investments", verbose_name=_("share class"))
    invest_date = models.DateField(_("investment date"))
    instrument = models.TextField(_("instrument"), choices=Instrument.choices)
    shares = models.BigIntegerField(_("shares"), null=True, blank=True,
                                    help_text=_("Required for equity. For warrants: shares obtainable."))
    price_per_share = price_field(_("price per share"))
    amount = money_field(_("amount"), null=False,
                         help_text=_("Cash invested; 0 for shares received on conversion."))
    currency = currency_field()
    fx_to_chf = models.DecimalField(_("FX to CHF"), max_digits=18, decimal_places=8, null=True, blank=True,
                                    help_text=_("CHF per unit of currency on the investment date."))
    converted_from = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True,
                                       related_name="conversions", verbose_name=_("converted from"))
    source_document = models.ForeignKey("Document", on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="+", verbose_name=_("source document"))
    reported_value = money_field(_("reported value"), help_text=_(
        "For a position without shares: its value as reported by the fund or the backoffice, in the investment "
        "currency."))
    reported_value_date = models.DateField(_("reported value as of"), null=True, blank=True)
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "investments"
        ordering = ["company", "invest_date", "id"]
        verbose_name = _("investment")
        verbose_name_plural = _("investments")
        constraints = [
            models.CheckConstraint(
                condition=Q(instrument__in=["equity", "convertible_loan", "safe", "warrant", "spv", "reported"]),
                name="investments_instrument_check",
            ),
            models.CheckConstraint(
                condition=~Q(instrument="equity") | (Q(shares__isnull=False) & Q(share_class__isnull=False)),
                name="investments_equity_shares_check",
            ),
            models.CheckConstraint(condition=Q(shares__isnull=True) | Q(shares__gt=0),
                                   name="investments_shares_positive_check"),
            models.CheckConstraint(condition=Q(amount__gte=0), name="investments_amount_check"),
            models.CheckConstraint(condition=Q(fx_to_chf__isnull=True) | Q(fx_to_chf__gt=0),
                                   name="investments_fx_check"),
            currency_check("investments_currency_check"),
        ]

    def __str__(self):
        return f"{self.company} {self.invest_date} {self.get_instrument_display()} {self.amount} {self.currency}"

    def clean(self):
        errors = {}
        _check_class_company(self, errors)
        if self.instrument == self.Instrument.EQUITY:
            if not self.shares:
                errors["shares"] = _("Equity investments need a number of shares.")
            if not self.share_class_id:
                errors["share_class"] = _("Equity investments need a share class.")
        if self.deal_id and self.company_id and self.deal.company_id != self.company_id:
            errors["deal"] = _("The deal belongs to another company.")
        if errors:
            raise ValidationError(errors)


class ConvertibleTerms(models.Model):
    investment = models.OneToOneField(Investment, on_delete=models.CASCADE, primary_key=True,
                                      related_name="convertible_terms", verbose_name=_("investment"))
    interest_rate = models.DecimalField(_("interest rate"), max_digits=6, decimal_places=4, null=True, blank=True,
                                        help_text=_("As a fraction: 0.08 = 8%."))
    discount_pct = models.DecimalField(_("discount"), max_digits=6, decimal_places=4, null=True, blank=True,
                                       help_text=_("As a fraction: 0.20 = 20%."))
    valuation_cap = money_field(_("valuation cap"))
    maturity_date = models.DateField(_("maturity date"), null=True, blank=True)
    converted_on = models.DateField(_("converted on"), null=True, blank=True)

    class Meta:
        db_table = "convertible_terms"
        verbose_name = _("convertible terms")
        verbose_name_plural = _("convertible terms")
        constraints = [
            models.CheckConstraint(
                condition=(Q(interest_rate__isnull=True) | Q(interest_rate__gte=0))
                & (Q(discount_pct__isnull=True) | (Q(discount_pct__gte=0) & Q(discount_pct__lt=1))),
                name="convertible_terms_rates_check",
            ),
        ]

    def __str__(self):
        return str(self.investment)


class Realization(models.Model):
    class Kind(models.TextChoices):
        SALE = "sale", _("Sale")
        DIVIDEND = "dividend", _("Dividend")
        DISTRIBUTION = "distribution", _("Distribution")
        ESCROW_RELEASE = "escrow_release", _("Escrow release")

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="realizations",
                                verbose_name=_("company"))
    entity = models.ForeignKey(InvestingEntity, on_delete=models.RESTRICT, related_name="realizations",
                               verbose_name=_("investing entity"))
    share_class = models.ForeignKey(ShareClass, on_delete=models.RESTRICT, null=True, blank=True,
                                    related_name="realizations", verbose_name=_("share class"))
    realized_on = models.DateField(_("realized on"))
    kind = models.TextField(_("kind"), choices=Kind.choices)
    shares_sold = models.BigIntegerField(_("shares sold"), null=True, blank=True)
    proceeds = money_field(_("proceeds"), null=False)
    currency = currency_field()
    fx_to_chf = models.DecimalField(_("FX to CHF"), max_digits=18, decimal_places=8, null=True, blank=True)
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "realizations"
        ordering = ["company", "realized_on", "id"]
        verbose_name = _("realization")
        verbose_name_plural = _("realizations")
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=["sale", "dividend", "distribution", "escrow_release"]),
                name="realizations_kind_check",
            ),
            models.CheckConstraint(
                condition=Q(shares_sold__isnull=True) | (Q(shares_sold__gt=0) & Q(share_class__isnull=False)),
                name="realizations_shares_sold_check",
            ),
            models.CheckConstraint(condition=Q(proceeds__gte=0), name="realizations_proceeds_check"),
            currency_check("realizations_currency_check"),
        ]

    def __str__(self):
        return f"{self.company} {self.realized_on} {self.get_kind_display()} {self.proceeds} {self.currency}"

    def clean(self):
        errors = {}
        _check_class_company(self, errors)
        if self.shares_sold and not self.share_class_id:
            errors["share_class"] = _("Say which share class was sold.")
        if errors:
            raise ValidationError(errors)


class Valuation(models.Model):
    class Method(models.TextChoices):
        ROUND = "round", _("Financing round")
        OFFER = "offer", _("Offer")
        INTERNAL_FAIR_VALUE = "internal_fair_value", _("Internal fair value")
        WRITE_OFF = "write_off", _("Write-off")

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="valuations",
                                verbose_name=_("company"))
    valuation_date = models.DateField(_("valuation date"))
    method = models.TextField(_("method"), choices=Method.choices)
    post_money = money_field(_("post-money valuation"))
    price_per_share = price_field(_("price per share"))
    share_class = models.ForeignKey(ShareClass, on_delete=models.RESTRICT, null=True, blank=True,
                                    related_name="valuations", verbose_name=_("share class"),
                                    help_text=_("Class the price per share refers to."))
    currency = currency_field()
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "valuations"
        ordering = ["company", "-valuation_date", "-id"]
        verbose_name = _("valuation")
        verbose_name_plural = _("valuations")
        constraints = [
            models.CheckConstraint(
                condition=Q(method__in=["round", "offer", "internal_fair_value", "write_off"]),
                name="valuations_method_check",
            ),
            models.CheckConstraint(
                condition=Q(method="write_off") | Q(post_money__isnull=False) | Q(price_per_share__isnull=False),
                name="valuations_value_present_check",
            ),
            currency_check("valuations_currency_check"),
        ]

    def __str__(self):
        return f"{self.company} {self.valuation_date} {self.get_method_display()}"

    def clean(self):
        errors = {}
        _check_class_company(self, errors)
        if errors:
            raise ValidationError(errors)
