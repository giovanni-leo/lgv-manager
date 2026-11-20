"""Automatic data gathering: Claude jobs, the suggestions they produce, inbound emails."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .parties import Company
from .pipeline import Deal


class InboundEmail(models.Model):
    """An email sent to the fund's intake mailbox (or uploaded as .eml)."""

    class Status(models.TextChoices):
        RECEIVED = "received", _("Received")
        PROCESSING = "processing", _("Processing")
        DONE = "done", _("Done")
        NEEDS_COMPANY = "needs_company", _("Company not recognised")
        REJECTED = "rejected", _("Sender not allowed")
        FAILED = "failed", _("Failed")

    message_id = models.TextField(_("message id"), unique=True)
    sender = models.TextField(_("from"))
    subject = models.TextField(_("subject"), blank=True)
    received_at = models.DateTimeField(_("received at"), default=timezone.now, db_default=Now())
    raw = models.FileField(_("original email"), upload_to="emails/%Y/%m/", max_length=512)
    status = models.TextField(_("status"), choices=Status.choices, default=Status.RECEIVED,
                              db_default=Status.RECEIVED)
    company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="inbound_emails", verbose_name=_("company"))
    deal = models.ForeignKey(Deal, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="inbound_emails", verbose_name=_("deal"))
    summary = models.TextField(_("summary"), blank=True, null=True)
    note = models.TextField(_("processing note"), blank=True, null=True)

    class Meta:
        db_table = "inbound_emails"
        ordering = ["-received_at"]
        verbose_name = _("inbound email")
        verbose_name_plural = _("inbound emails")
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["received", "processing", "done", "needs_company", "rejected", "failed"]),
                name="inbound_emails_status_check",
            ),
        ]

    def __str__(self):
        return f"{self.sender}: {self.subject}"


class AiJob(models.Model):
    """A unit of work for the background worker that calls Claude."""

    class Kind(models.TextChoices):
        WEB_RESEARCH = "web_research", _("Web research")
        EMAIL = "email", _("Email analysis")

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        RUNNING = "running", _("Running")
        DONE = "done", _("Done")
        FAILED = "failed", _("Failed")

    kind = models.TextField(_("kind"), choices=Kind.choices)
    status = models.TextField(_("status"), choices=Status.choices, default=Status.QUEUED, db_default=Status.QUEUED)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="ai_jobs", verbose_name=_("company"))
    deal = models.ForeignKey(Deal, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="ai_jobs", verbose_name=_("deal"))
    inbound_email = models.ForeignKey(InboundEmail, on_delete=models.CASCADE, null=True, blank=True,
                                      related_name="jobs", verbose_name=_("inbound email"))
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name="+", verbose_name=_("requested by"))
    created_at = models.DateTimeField(_("created at"), default=timezone.now, db_default=Now())
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)
    error = models.TextField(_("error"), blank=True, null=True)
    summary = models.TextField(_("summary"), blank=True, null=True)
    input_tokens = models.IntegerField(_("input tokens"), default=0, db_default=0)
    output_tokens = models.IntegerField(_("output tokens"), default=0, db_default=0)
    web_searches = models.IntegerField(_("web searches"), default=0, db_default=0)

    class Meta:
        db_table = "ai_jobs"
        ordering = ["-created_at"]
        verbose_name = _("Claude job")
        verbose_name_plural = _("Claude jobs")
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=["web_research", "email"]), name="ai_jobs_kind_check"),
            models.CheckConstraint(condition=Q(status__in=["queued", "running", "done", "failed"]),
                                   name="ai_jobs_status_check"),
        ]
        indexes = [models.Index(fields=["status", "created_at"], name="ai_jobs_status_idx")]

    def __str__(self):
        return f"{self.get_kind_display()} #{self.pk} ({self.get_status_display()})"

    @property
    def is_active(self):
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)

    @property
    def cost_usd(self):
        """Approximate model cost (token prices from settings; web search fees not included)."""
        return (self.input_tokens * settings.AI_PRICE_INPUT_PER_MTOK
                + self.output_tokens * settings.AI_PRICE_OUTPUT_PER_MTOK) / 1_000_000


class Suggestion(models.Model):
    """One proposed change, waiting for a person to accept or reject it."""

    class Target(models.TextChoices):
        COMPANY_FIELD = "company_field", _("Company detail")
        DEAL_FIELD = "deal_field", _("Round detail")
        DEAL_NOTE = "deal_note", _("Investment terms")
        PARTICIPANT = "participant", _("Investor in the round")
        CONTACT = "contact", _("Contact")
        PAST_ROUND = "past_round", _("Past financing round")
        NEWS = "news", _("News")
        EVENT = "event", _("Upcoming event")
        FIGURES = "figures", _("Company figures")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACCEPTED = "accepted", _("Accepted")
        REJECTED = "rejected", _("Rejected")

    job = models.ForeignKey(AiJob, on_delete=models.CASCADE, related_name="suggestions", verbose_name=_("job"))
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="suggestions", verbose_name=_("company"))
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="suggestions", verbose_name=_("deal"))
    target = models.TextField(_("type"), choices=Target.choices)
    payload = models.JSONField(_("proposed data"))
    label = models.TextField(_("proposal"))
    source_url = models.TextField(_("source"), blank=True, null=True)
    evidence = models.TextField(_("evidence"), blank=True, null=True)
    confidence = models.TextField(_("confidence"), blank=True, null=True)
    status = models.TextField(_("status"), choices=Status.choices, default=Status.PENDING,
                              db_default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+", verbose_name=_("decided by"))
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), default=timezone.now, db_default=Now())

    class Meta:
        db_table = "suggestions"
        ordering = ["created_at", "id"]
        verbose_name = _("suggestion")
        verbose_name_plural = _("suggestions")
        constraints = [
            models.CheckConstraint(
                condition=Q(target__in=["company_field", "deal_field", "deal_note", "participant", "contact",
                                        "past_round", "news", "event", "figures"]),
                name="suggestions_target_check",
            ),
            models.CheckConstraint(condition=Q(status__in=["pending", "accepted", "rejected"]),
                                   name="suggestions_status_check"),
            models.CheckConstraint(condition=Q(company__isnull=False) | Q(deal__isnull=False),
                                   name="suggestions_owner_check"),
        ]
        indexes = [models.Index(fields=["company", "status"], name="suggestions_company_status_idx")]

    def __str__(self):
        return self.label
