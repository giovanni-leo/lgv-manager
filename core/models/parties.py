"""Investing entities, companies, contacts and FX rates."""

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .common import created_at_field, currency_check, currency_field, updated_at_field


class InvestingEntity(models.Model):
    """A vehicle that invests, normally the fund itself (e.g. Lake Growth Ventures).

    Several entities are supported for parallel or co-investment vehicles; with a
    single entity the interface selects it automatically and hides the field.
    """

    class Kind(models.TextChoices):
        COMPANY = "company", _("Company")
        PERSONAL = "personal", _("Personal")
        FUND = "fund", _("Fund")

    name = models.TextField(_("name"), unique=True)
    kind = models.TextField(_("kind"), choices=Kind.choices)
    base_currency = currency_field(verbose_name=_("base currency"))
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "investing_entities"
        ordering = ["name"]
        verbose_name = _("investing entity")
        verbose_name_plural = _("investing entities")
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=["company", "personal", "fund"]),
                name="investing_entities_kind_check",
            ),
            currency_check("investing_entities_currency_check", "base_currency"),
        ]

    def __str__(self):
        return self.name


class BoardRole(models.TextChoices):
    NONE = "none", _("None")
    OBSERVER = "observer", _("Observer")
    MEMBER = "member", _("Board member")


class ReportingFrequency(models.TextChoices):
    MONTHLY = "monthly", _("Monthly")
    QUARTERLY = "quarterly", _("Quarterly")
    SEMIANNUAL = "semiannual", _("Every six months")
    ANNUAL = "annual", _("Annual")


class Company(models.Model):
    class Status(models.TextChoices):
        PROSPECT = "prospect", _("Prospect")
        PORTFOLIO = "portfolio", _("Portfolio")
        EXITED = "exited", _("Exited")
        WRITTEN_OFF = "written_off", _("Written off")
        ARCHIVED = "archived", _("Archived")

    name = models.TextField(_("name"))
    legal_name = models.TextField(_("legal name"), blank=True, null=True)
    country = models.CharField(_("country"), max_length=2, blank=True, null=True,
                               help_text=_("ISO 3166 two-letter code, e.g. CH"))
    city = models.TextField(_("city"), blank=True, null=True)
    website = models.TextField(_("website"), blank=True, null=True)
    sector = models.TextField(_("sector"), blank=True, null=True,
                              help_text=_("e.g. medical devices, diagnostics, digital health"))
    description = models.TextField(_("description"), blank=True, null=True)
    status = models.TextField(_("status"), choices=Status.choices,
                              default=Status.PROSPECT, db_default=Status.PROSPECT)
    board_role = models.TextField(_("our board role"), choices=BoardRole.choices,
                                  default=BoardRole.NONE, db_default=BoardRole.NONE)
    reporting_frequency = models.TextField(_("reporting frequency"), choices=ReportingFrequency.choices,
                                           null=True, blank=True)
    data_room_url = models.TextField(_("data room / reporting link"), null=True, blank=True,
                                     help_text=_("Where the company shares reports, minutes and the cap table."))
    created_at = created_at_field()
    updated_at = updated_at_field()

    class Meta:
        db_table = "companies"
        ordering = ["name"]
        verbose_name = _("company")
        verbose_name_plural = _("companies")
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["prospect", "portfolio", "exited", "written_off", "archived"]),
                name="companies_status_check",
            ),
            models.CheckConstraint(condition=Q(board_role__in=["none", "observer", "member"]),
                                   name="companies_board_role_check"),
            models.CheckConstraint(
                condition=Q(reporting_frequency__isnull=True)
                | Q(reporting_frequency__in=["monthly", "quarterly", "semiannual", "annual"]),
                name="companies_reporting_frequency_check",
            ),
        ]

    def __str__(self):
        return self.name


class CoInvestor(models.Model):
    """Another investor in a round: a VC, a strategic (corporate) investor, an angel..."""

    class Kind(models.TextChoices):
        VC = "vc", _("VC")
        STRATEGIC = "strategic", _("Strategic")
        ANGEL = "angel", _("Angel")
        FAMILY_OFFICE = "family_office", _("Family office")
        PUBLIC = "public", _("Public funding")
        OTHER = "other", _("Other")

    name = models.TextField(_("name"), unique=True)
    kind = models.TextField(_("type"), choices=Kind.choices, default=Kind.VC, db_default=Kind.VC)
    website = models.TextField(_("website"), blank=True, null=True)
    city = models.TextField(_("city"), blank=True, null=True)
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "co_investors"
        ordering = ["name"]
        verbose_name = _("co-investor")
        verbose_name_plural = _("co-investors")
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=["vc", "strategic", "angel", "family_office", "public", "other"]),
                name="co_investors_kind_check",
            ),
        ]

    def __str__(self):
        return self.name


class Contact(models.Model):
    full_name = models.TextField(_("full name"))
    email = models.TextField(_("email"), blank=True, null=True)
    phone = models.TextField(_("phone"), blank=True, null=True)
    role = models.TextField(_("role"), blank=True, null=True)
    company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="contacts", verbose_name=_("company"))
    co_investor = models.ForeignKey(CoInvestor, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="contacts", verbose_name=_("co-investor"),
                                    help_text=_("For people working at another investor."))
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "contacts"
        ordering = ["full_name"]
        verbose_name = _("contact")
        verbose_name_plural = _("contacts")

    def __str__(self):
        return self.full_name


class FxRate(models.Model):
    """CHF per unit of a foreign currency on a given date.

    The reference schema uses (rate_date, currency) as primary key. Django admin
    does not support composite primary keys, so a surrogate id is used and the
    pair is enforced as a unique constraint.
    """

    rate_date = models.DateField(_("rate date"))
    currency = models.CharField(_("currency"), max_length=3)
    chf_per_unit = models.DecimalField(_("CHF per unit"), max_digits=18, decimal_places=8)

    class Meta:
        db_table = "fx_rates"
        ordering = ["-rate_date", "currency"]
        verbose_name = _("exchange rate")
        verbose_name_plural = _("exchange rates")
        constraints = [
            models.UniqueConstraint(fields=["rate_date", "currency"], name="fx_rates_date_currency_key"),
            models.CheckConstraint(condition=Q(chf_per_unit__gt=0), name="fx_rates_positive_check"),
            currency_check("fx_rates_currency_check"),
        ]

    def __str__(self):
        return f"{self.currency} {self.rate_date}: {self.chf_per_unit}"
