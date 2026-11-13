"""Document metadata. File contents live in storage (disk volume or S3)."""

import hashlib
import mimetypes
import uuid
from pathlib import PurePath

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import Q, Value
from django.db.models.functions import Coalesce, Concat, Now
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .captable import Investment, ShareEvent
from .monitoring import Interaction
from .parties import Company
from .pipeline import Deal


def document_storage_key(instance, filename):
    """Opaque storage key: never derived from user data except the extension."""
    suffix = PurePath(filename).suffix.lower()[:10]
    now = timezone.now()
    return f"documents/{now:%Y/%m}/{uuid.uuid4().hex}{suffix}"


DOCUMENT_CATEGORIES = [
    "pitch_deck", "term_sheet", "shareholders_agreement", "subscription", "board_minutes",
    "financials", "investor_report", "general_assembly", "cap_table", "regulatory", "legal", "other",
]


class Document(models.Model):
    class Category(models.TextChoices):
        PITCH_DECK = "pitch_deck", _("Pitch deck")
        TERM_SHEET = "term_sheet", _("Term sheet")
        SHAREHOLDERS_AGREEMENT = "shareholders_agreement", _("Shareholders' agreement")
        SUBSCRIPTION = "subscription", _("Subscription agreement")
        BOARD_MINUTES = "board_minutes", _("Board minutes")
        FINANCIALS = "financials", _("Financial statements")
        INVESTOR_REPORT = "investor_report", _("Investor report")
        GENERAL_ASSEMBLY = "general_assembly", _("General assembly minutes")
        CAP_TABLE = "cap_table", _("Cap table")
        REGULATORY = "regulatory", _("Regulatory")
        LEGAL = "legal", _("Legal")
        OTHER = "other", _("Other")

    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="documents", verbose_name=_("company"))
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="documents", verbose_name=_("deal"))
    category = models.TextField(_("category"), choices=Category.choices,
                                default=Category.OTHER, db_default=Category.OTHER)
    title = models.TextField(_("title"))
    original_filename = models.TextField(_("original filename"), editable=False)
    # The schema's storage_key column holds the storage path of the file.
    file = models.FileField(_("file"), db_column="storage_key", max_length=512, unique=True,
                            upload_to=document_storage_key)
    mime_type = models.TextField(_("MIME type"), null=True, blank=True, editable=False)
    size_bytes = models.BigIntegerField(_("size (bytes)"), null=True, blank=True, editable=False)
    sha256 = models.CharField(_("SHA-256"), max_length=64, editable=False)
    document_date = models.DateField(_("document date"), null=True, blank=True)
    uploaded_at = models.DateTimeField(_("uploaded at"), default=timezone.now, db_default=Now(), editable=False)
    extracted_text = models.TextField(_("extracted text"), null=True, blank=True, editable=False)
    search = models.GeneratedField(
        expression=SearchVector(
            Concat(Coalesce("title", Value("", output_field=models.TextField())), Value(" ", output_field=models.TextField()), Coalesce("extracted_text", Value("", output_field=models.TextField())),
                   output_field=models.TextField()),
            config="simple",
        ),
        output_field=SearchVectorField(),
        db_persist=True,
    )
    notes = models.TextField(_("notes"), blank=True, null=True)

    class Meta:
        db_table = "documents"
        ordering = ["-document_date", "-uploaded_at"]
        verbose_name = _("document")
        verbose_name_plural = _("documents")
        constraints = [
            models.CheckConstraint(
                condition=Q(category__in=DOCUMENT_CATEGORIES), name="documents_category_check"
            ),
            models.CheckConstraint(
                condition=Q(company__isnull=False) | Q(deal__isnull=False), name="documents_owner_check"
            ),
            models.CheckConstraint(condition=Q(sha256__regex=r"^[0-9a-f]{64}$"),
                                   name="documents_sha256_check"),
            # NULLS NOT DISTINCT: also blocks duplicates among deal-only documents.
            models.UniqueConstraint(fields=["sha256", "company"], nulls_distinct=False,
                                    name="documents_sha_company_idx"),
        ]
        indexes = [
            GinIndex(fields=["search"], name="documents_search_idx"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        """Fill in file metadata (hash, size, MIME type) when a new file is attached."""
        upload = self.file
        if upload and not getattr(upload, "_committed", True):
            digest = hashlib.sha256()
            upload.seek(0)
            for chunk in upload.chunks():
                digest.update(chunk)
            upload.seek(0)
            self.sha256 = digest.hexdigest()
            self.size_bytes = upload.size
            self.original_filename = PurePath(upload.name).name
            self.mime_type = (getattr(upload.file, "content_type", None)
                              or mimetypes.guess_type(upload.name)[0])
            if not self.title:
                self.title = PurePath(upload.name).stem
        super().save(*args, **kwargs)


class DocumentLink(models.Model):
    """Extra links from a document to an investment, cap-table event or interaction."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="links",
                                 verbose_name=_("document"))
    investment = models.ForeignKey(Investment, on_delete=models.CASCADE, null=True, blank=True,
                                   related_name="document_links", verbose_name=_("investment"))
    share_event = models.ForeignKey(ShareEvent, on_delete=models.CASCADE, null=True, blank=True,
                                    related_name="document_links", verbose_name=_("share event"))
    interaction = models.ForeignKey(Interaction, on_delete=models.CASCADE, null=True, blank=True,
                                    related_name="document_links", verbose_name=_("interaction"))

    class Meta:
        db_table = "document_links"
        verbose_name = _("document link")
        verbose_name_plural = _("document links")
        constraints = [
            models.CheckConstraint(
                condition=(Q(investment__isnull=False, share_event__isnull=True, interaction__isnull=True)
                           | Q(investment__isnull=True, share_event__isnull=False, interaction__isnull=True)
                           | Q(investment__isnull=True, share_event__isnull=True, interaction__isnull=False)),
                name="document_links_one_target_check",
            ),
            models.UniqueConstraint(fields=["document", "investment"], name="document_links_investment_key"),
            models.UniqueConstraint(fields=["document", "share_event"], name="document_links_share_event_key"),
            models.UniqueConstraint(fields=["document", "interaction"], name="document_links_interaction_key"),
        ]

    def __str__(self):
        target = self.investment or self.share_event or self.interaction
        return f"{self.document} → {target}"
