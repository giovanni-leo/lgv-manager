"""Company metrics, milestones and the interaction log."""

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .common import currency_check, currency_field, money_field
from .parties import Company, Contact
from .pipeline import Deal


class CompanyMetric(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="metrics",
                                verbose_name=_("company"))
    as_of = models.DateField(_("as of"))
    cash = money_field(_("cash"))
    monthly_burn = money_field(_("monthly burn"))
    revenue_ttm = money_field(_("revenue (TTM)"))
    headcount = models.IntegerField(_("headcount"), null=True, blank=True)
    currency = currency_field()
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "company_metrics"
        ordering = ["company", "-as_of"]
        verbose_name = _("company metric")
        verbose_name_plural = _("company metrics")
        constraints = [
            models.UniqueConstraint(fields=["company", "as_of"], name="company_metrics_company_id_as_of_key"),
            models.CheckConstraint(condition=Q(headcount__isnull=True) | Q(headcount__gte=0),
                                   name="company_metrics_headcount_check"),
            currency_check("company_metrics_currency_check"),
        ]

    def __str__(self):
        return f"{self.company} {self.as_of}"


class Milestone(models.Model):
    class Category(models.TextChoices):
        REGULATORY = "regulatory", _("Regulatory")
        CLINICAL = "clinical", _("Clinical")
        COMMERCIAL = "commercial", _("Commercial")
        FINANCING = "financing", _("Financing")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        PLANNED = "planned", _("Planned")
        ACHIEVED = "achieved", _("Achieved")
        DELAYED = "delayed", _("Delayed")
        ABANDONED = "abandoned", _("Abandoned")

    class Kind(models.TextChoices):
        """Standard medtech milestones (optional, refines the category)."""

        FIRST_IN_HUMAN = "first_in_human", _("First-in-human study")
        PIVOTAL_STUDY = "pivotal_study", _("Pivotal study")
        CE_MARK = "ce_mark", _("CE marking")
        FDA_510K = "fda_510k", _("FDA 510(k)")
        FDA_DE_NOVO = "fda_de_novo", _("FDA De Novo")
        FDA_PMA = "fda_pma", _("FDA PMA")

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="milestones",
                                verbose_name=_("company"))
    title = models.TextField(_("title"))
    category = models.TextField(_("category"), choices=Category.choices)
    kind = models.TextField(_("standard milestone"), choices=Kind.choices, null=True, blank=True)
    target_date = models.DateField(_("target date"), null=True, blank=True)
    achieved_on = models.DateField(_("achieved on"), null=True, blank=True)
    status = models.TextField(_("status"), choices=Status.choices,
                              default=Status.PLANNED, db_default=Status.PLANNED)
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "milestones"
        ordering = ["company", "target_date", "id"]
        verbose_name = _("milestone")
        verbose_name_plural = _("milestones")
        constraints = [
            models.CheckConstraint(
                condition=Q(category__in=["regulatory", "clinical", "commercial", "financing", "other"]),
                name="milestones_category_check",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["planned", "achieved", "delayed", "abandoned"]),
                name="milestones_status_check",
            ),
            models.CheckConstraint(
                condition=Q(kind__isnull=True) | Q(kind__in=["first_in_human", "pivotal_study", "ce_mark", "fda_510k", "fda_de_novo", "fda_pma"]),
                name="milestones_kind_check",
            ),
        ]

    def __str__(self):
        return self.title


class Interaction(models.Model):
    class Kind(models.TextChoices):
        MEETING = "meeting", _("Meeting")
        BOARD = "board", _("Board meeting")
        CALL = "call", _("Call")
        EMAIL = "email", _("Email")
        NEWS = "news", _("News")
        NOTE = "note", _("Note")

    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="interactions", verbose_name=_("company"))
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="interactions", verbose_name=_("deal"))
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="interactions", verbose_name=_("contact"))
    occurred_on = models.DateField(_("date"))
    kind = models.TextField(_("kind"), choices=Kind.choices)
    summary = models.TextField(_("summary"))
    details = models.TextField(_("details"), blank=True, null=True)

    class Meta:
        db_table = "interactions"
        ordering = ["-occurred_on", "-id"]
        verbose_name = _("interaction")
        verbose_name_plural = _("interactions")
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=["meeting", "board", "call", "email", "news", "note"]),
                name="interactions_kind_check",
            ),
        ]

    def __str__(self):
        return f"{self.occurred_on} {self.summary[:60]}"
