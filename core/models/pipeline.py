"""Deal pipeline."""

from django.db import models, transaction
from django.db.models import Q
from django.db.models.functions import Now
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .common import (created_at_field, currency_check, currency_field, money_field,
                     today, updated_at_field)
from .parties import Company, Contact


class DealStage(models.TextChoices):
    SOURCED = "sourced", _("Sourced")
    FIRST_MEETING = "first_meeting", _("First meeting")
    ANALYSIS = "analysis", _("Analysis")
    DUE_DILIGENCE = "due_diligence", _("Due diligence")
    TERM_SHEET = "term_sheet", _("Term sheet")
    INVESTED = "invested", _("Invested")
    ON_HOLD = "on_hold", _("On hold")
    PASSED = "passed", _("Passed")


# Ordered funnel stages; on_hold and passed are outside the funnel.
FUNNEL_STAGES = [
    DealStage.SOURCED, DealStage.FIRST_MEETING, DealStage.ANALYSIS,
    DealStage.DUE_DILIGENCE, DealStage.TERM_SHEET, DealStage.INVESTED,
]
STAGE_VALUES = [s.value for s in DealStage]


class Deal(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="deals",
                                verbose_name=_("company"))
    stage = models.TextField(_("stage"), choices=DealStage.choices,
                             default=DealStage.SOURCED, db_default=DealStage.SOURCED)
    source_contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="sourced_deals", verbose_name=_("source contact"))
    source_note = models.TextField(_("source note"), blank=True, null=True)
    round_name = models.TextField(_("round"), blank=True, null=True,
                                  help_text=_("Pre-seed, Seed, Series A, ..."))
    round_size = money_field(_("round size"))
    pre_money = money_field(_("pre-money valuation"))
    proposed_ticket = money_field(_("proposed ticket"))
    currency = currency_field()
    next_step = models.TextField(_("next step"), blank=True, null=True)
    next_step_due = models.DateField(_("next step due"), blank=True, null=True)
    pass_reason = models.TextField(_("pass reason"), blank=True, null=True)
    opened_on = models.DateField(_("opened on"), default=today, db_default=Now())
    closed_on = models.DateField(_("closed on"), blank=True, null=True)
    notes = models.TextField(_("notes"), blank=True, null=True)
    created_at = created_at_field()
    updated_at = updated_at_field()

    class Meta:
        db_table = "deals"
        ordering = ["-opened_on", "-id"]
        verbose_name = _("deal")
        verbose_name_plural = _("deals")
        constraints = [
            models.CheckConstraint(condition=Q(stage__in=STAGE_VALUES), name="deals_stage_check"),
            currency_check("deals_currency_check"),
        ]
        indexes = [models.Index(fields=["stage"], name="deals_stage_idx")]

    def __str__(self):
        label = self.company.name if self.company_id else "?"
        return f"{label} – {self.round_name}" if self.round_name else label

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_stage = instance.__dict__.get("stage")
        return instance

    def save(self, *args, **kwargs):
        """Persist the deal and log every stage change in deal_stage_history."""
        with transaction.atomic():
            previous = getattr(self, "_loaded_stage", None)
            if not self._state.adding and previous is None:
                previous = Deal.objects.filter(pk=self.pk).values_list("stage", flat=True).first()
            adding = self._state.adding
            changed = adding or previous != self.stage
            super().save(*args, **kwargs)
            if changed:
                DealStageHistory.objects.create(
                    deal=self, from_stage=None if adding else previous,
                    to_stage=self.stage, changed_at=getattr(self, "_stage_changed_at", None) or timezone.now(),
                )
            self._loaded_stage = self.stage


class DealStageHistory(models.Model):
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="stage_history",
                             verbose_name=_("deal"))
    from_stage = models.TextField(_("from stage"), choices=DealStage.choices, blank=True, null=True)
    to_stage = models.TextField(_("to stage"), choices=DealStage.choices)
    changed_at = models.DateTimeField(_("changed at"), default=timezone.now, db_default=Now())

    class Meta:
        db_table = "deal_stage_history"
        ordering = ["deal", "changed_at", "id"]
        verbose_name = _("stage change")
        verbose_name_plural = _("stage history")
        constraints = [
            models.CheckConstraint(
                condition=Q(to_stage__in=STAGE_VALUES)
                & (Q(from_stage__isnull=True) | Q(from_stage__in=STAGE_VALUES)),
                name="deal_stage_history_stage_check",
            ),
        ]

    def __str__(self):
        return f"{self.from_stage or '–'} → {self.to_stage}"
