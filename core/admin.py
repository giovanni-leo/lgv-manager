"""Data-entry interface.

Design rule: every form shows only the essential fields; everything else sits
in a collapsed "More details" section, and related tables on a company page
start collapsed with a few columns and a link to the full record.
"""

from django import forms
from django.contrib import admin
from django.db import models
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from .models import (CoInvestor, Company, CompanyMetric, Contact, RoundParticipant, ConvertibleTerms, Deal, DealStageHistory,
                     Document, DocumentLink, FxRate, InvestingEntity, Investment, Interaction,
                     Milestone, OptionPool, Realization, ShareClass, ShareEvent, Valuation)
from . import admin_users  # noqa: F401  (registers the simplified user admin)
from .services.ownership import ownership_by_class, ownership_company


def _fmt_num(value, decimals=0):
    if value is None:
        return "–"
    return f"{value:,.{decimals}f}".replace(",", "'")  # Swiss thousands separator


def _fmt_pct(value):
    return "–" if value is None else f"{value:.2f}%"


LONG_TEXT_FIELDS = {"notes", "description", "details", "source_note", "pass_reason", "next_step", "summary"}


class CompactTextMixin:
    """TEXT columns mostly hold short values (names, cities): edit them on one line."""

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, models.TextField) and not db_field.choices:
            rows = 3 if db_field.name in LONG_TEXT_FIELDS else None
            kwargs["widget"] = (forms.Textarea(attrs={"rows": rows, "cols": 60}) if rows
                                else forms.TextInput(attrs={"size": 40}))
        return super().formfield_for_dbfield(db_field, request, **kwargs)


class ModelAdmin(CompactTextMixin, admin.ModelAdmin):
    """Shows ``essential`` fields first; all other fields in a collapsed section."""

    essential: list[str] = []
    more_label = _("More details")

    def get_fieldsets(self, request, obj=None):
        if self.fieldsets or not self.essential:
            return super().get_fieldsets(request, obj)
        fields = list(self.get_fields(request, obj))
        main = [f for f in self.get_essential(request, obj) if f in fields]
        rest = [f for f in fields if f not in main]
        fieldsets = [(None, {"fields": main})]
        if rest:
            fieldsets.append((self.more_label, {"fields": rest, "classes": ["collapse"]}))
        return fieldsets


    def get_essential(self, request, obj=None):
        return self.essential

    # Coming from an at-a-glance page (?next=/companies/3/): go back there after saving.
    def _next_url(self, request):
        url = request.GET.get("next")
        if url and url_has_allowed_host_and_scheme(url, {request.get_host()}, request.is_secure()):
            return url
        return None

    def response_add(self, request, obj, post_url_continue=None):
        url = self._next_url(request)
        if url and not {"_continue", "_addanother"} & set(request.POST):
            return redirect(url)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        url = self._next_url(request)
        if url and not {"_continue", "_addanother", "_saveasnew"} & set(request.POST):
            return redirect(url)
        return super().response_change(request, obj)

    def response_delete(self, request, obj_display, obj_id):
        url = self._next_url(request)
        if url:
            return redirect(url)
        return super().response_delete(request, obj_display, obj_id)


def single_entity_id():
    """The only investing entity, if there is exactly one (the usual case: the fund)."""
    ids = list(InvestingEntity.objects.values_list("pk", flat=True)[:2])
    return ids[0] if len(ids) == 1 else None


class EntityFieldMixin:
    """With a single investing entity, fill it in automatically and keep it out of sight."""

    def get_changeform_initial_data(self, request):
        data = super().get_changeform_initial_data(request)
        entity_id = single_entity_id()
        if entity_id:
            data.setdefault("entity", entity_id)
        return data

    def get_essential(self, request, obj=None):
        essential = super().get_essential(request, obj)
        return [f for f in essential if f != "entity"] if single_entity_id() else essential

    def get_list_display(self, request):
        columns = super().get_list_display(request)
        return [c for c in columns if c != "entity"] if single_entity_id() else columns

    def get_list_filter(self, request):
        filters = super().get_list_filter(request)
        return [f for f in filters if f != "entity"] if single_entity_id() else filters


class TabularInline(CompactTextMixin, admin.TabularInline):
    extra = 0
    show_change_link = True  # full record, with every field, one click away
    classes = ["collapse"]


class StackedInline(CompactTextMixin, admin.StackedInline):
    extra = 0
    classes = ["collapse"]


# --- Inlines on the company page ---------------------------------------------

class ShareClassInline(TabularInline):
    model = ShareClass
    fields = ["name", "is_common", "conversion_ratio"]


class ShareEventInline(TabularInline):
    model = ShareEvent
    fields = ["event_date", "share_class", "event_type", "shares_delta", "split_ratio"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "share_class" and getattr(request, "_admin_company_id", None):
            kwargs["queryset"] = ShareClass.objects.filter(company_id=request._admin_company_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class OptionPoolInline(TabularInline):
    model = OptionPool
    fields = ["as_of", "authorized", "warrants"]


class ValuationInline(TabularInline):
    model = Valuation
    fields = ["valuation_date", "method", "price_per_share", "post_money", "currency"]


class CompanyMetricInline(TabularInline):
    model = CompanyMetric
    fields = ["as_of", "cash", "monthly_burn", "currency"]


class MilestoneInline(TabularInline):
    model = Milestone
    fields = ["title", "category", "target_date", "status"]


class ConvertibleTermsInline(StackedInline):
    model = ConvertibleTerms
    max_num = 1


class RoundParticipantInline(TabularInline):
    model = RoundParticipant
    fields = ["co_investor", "role", "status", "amount", "currency"]
    autocomplete_fields = ["co_investor"]
    show_change_link = False


class DealStageHistoryInline(TabularInline):
    model = DealStageHistory
    can_delete = False
    show_change_link = False
    readonly_fields = ["from_stage", "to_stage", "changed_at"]
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


class DocumentLinkInline(TabularInline):
    model = DocumentLink
    show_change_link = False
    autocomplete_fields = ["investment", "share_event", "interaction"]


# --- Model admins -------------------------------------------------------------

@admin.register(Company)
class CompanyAdmin(ModelAdmin):
    list_display = ["name", "status", "sector"]
    list_filter = ["status"]
    search_fields = ["name", "legal_name", "description"]
    readonly_fields = ["cap_table", "created_at", "updated_at"]
    essential = ["name", "status", "sector", "cap_table"]
    inlines = [ShareClassInline, ShareEventInline, OptionPoolInline, ValuationInline,
               CompanyMetricInline, MilestoneInline]

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        return fields if obj else [f for f in fields if f != "cap_table"]

    def get_object(self, request, object_id, from_field=None):
        obj = super().get_object(request, object_id, from_field)
        request._admin_company_id = obj.pk if obj else None
        return obj

    @admin.display(description=_("Cap table today"))
    def cap_table(self, obj):
        if not obj or not obj.pk:
            return "–"
        rows = ownership_by_class(obj.pk, timezone.localdate())
        if not rows:
            return _("No share classes yet.")
        def warnings(r):
            return ", ".join(str(w) for w in [
                _("my shares exceed the class") if r.inconsistent else None,
                _("issuance events missing") if r.missing_issuance else None,
                _("negative position") if r.negative_position else None] if w)

        body = format_html_join(
            "", "<tr><td>{}{}</td><td style='text-align:right'>{}</td>"
                "<td style='text-align:right'>{}</td><td style='text-align:right'>{}</td></tr>",
            ((r.share_class, " ⚠" if r.has_warning else "", _fmt_num(r.shares_outstanding),
              _fmt_num(r.my_shares), _fmt_pct(r.pct_of_class)) for r in rows),
        )
        alerts = format_html_join(
            "", "<p class='errornote'>⚠ {}: {}</p>",
            ((r.share_class, warnings(r)) for r in rows if r.has_warning),
        )
        total = ownership_company(obj.pk, timezone.localdate())
        footer = ""
        if total:
            footer = format_html(
                "<p><b>{}</b> {} · <b>{}</b> {}</p>"
                "<details><summary>{}</summary><p>{}: {} · {}: {} · {}: {}</p></details>",
                _fmt_pct(total.pct_fully_diluted), _("fully diluted"),
                _fmt_pct(total.pct_basic), _("basic"),
                _("More details"),
                _("Option pool"), _fmt_num(total.option_pool),
                _("Warrants"), _fmt_num(total.warrants),
                _("Fully diluted shares"), _fmt_num(total.fully_diluted_total),
            )
        return format_html(
            "<table><thead><tr><th>{}</th><th>{}</th><th>{}</th><th>{}</th></tr></thead>"
            "<tbody>{}</tbody></table>{}{}",
            _("Class"), _("Outstanding"), _("Mine"), _("% of class"), body, footer, alerts,
        )


@admin.register(Deal)
class DealAdmin(ModelAdmin):
    list_display = ["company", "stage", "round_name", "next_step_due"]
    list_filter = ["stage"]
    search_fields = ["company__name", "round_name", "notes"]
    autocomplete_fields = ["company", "source_contact"]
    essential = ["company", "stage", "round_name", "round_size", "pre_money", "proposed_ticket", "currency",
                 "deadline", "next_step", "next_step_due"]
    inlines = [RoundParticipantInline, DealStageHistoryInline]


@admin.register(Investment)
class InvestmentAdmin(EntityFieldMixin, ModelAdmin):
    list_display = ["invest_date", "company", "entity", "instrument", "amount", "currency"]
    list_filter = ["entity", "instrument"]
    search_fields = ["company__name", "notes"]
    autocomplete_fields = ["company", "deal", "share_class", "converted_from", "source_document"]
    essential = ["company", "entity", "invest_date", "instrument", "share_class", "shares", "amount", "currency"]
    inlines = [ConvertibleTermsInline]


@admin.register(Realization)
class RealizationAdmin(EntityFieldMixin, ModelAdmin):
    list_display = ["realized_on", "company", "kind", "proceeds", "currency"]
    list_filter = ["kind"]
    search_fields = ["company__name", "notes"]
    autocomplete_fields = ["company", "share_class"]
    essential = ["company", "entity", "realized_on", "kind", "share_class", "shares_sold", "proceeds", "currency"]


@admin.register(Valuation)
class ValuationAdmin(ModelAdmin):
    list_display = ["valuation_date", "company", "method", "price_per_share"]
    list_filter = ["method"]
    search_fields = ["company__name"]
    autocomplete_fields = ["company", "share_class"]
    essential = ["company", "valuation_date", "method", "price_per_share", "post_money", "currency"]


@admin.register(ShareClass)
class ShareClassAdmin(ModelAdmin):
    list_display = ["name", "company", "conversion_ratio"]
    search_fields = ["name", "company__name"]
    autocomplete_fields = ["company"]
    essential = ["company", "name", "is_common", "conversion_ratio"]


@admin.register(ShareEvent)
class ShareEventAdmin(ModelAdmin):
    list_display = ["event_date", "company", "share_class", "event_type", "shares_delta"]
    list_filter = ["event_type"]
    search_fields = ["company__name", "share_class__name", "notes"]
    autocomplete_fields = ["company", "share_class", "source_document"]
    essential = ["company", "share_class", "event_date", "event_type", "shares_delta", "split_ratio"]


@admin.register(OptionPool)
class OptionPoolAdmin(ModelAdmin):
    list_display = ["company", "as_of", "authorized", "warrants"]
    autocomplete_fields = ["company"]
    essential = ["company", "as_of", "authorized", "warrants"]


@admin.register(CompanyMetric)
class CompanyMetricAdmin(ModelAdmin):
    list_display = ["company", "as_of", "cash", "monthly_burn"]
    search_fields = ["company__name"]
    autocomplete_fields = ["company"]
    essential = ["company", "as_of", "cash", "monthly_burn", "currency"]


@admin.register(Milestone)
class MilestoneAdmin(ModelAdmin):
    list_display = ["title", "company", "target_date", "status"]
    list_filter = ["status"]
    search_fields = ["title", "company__name"]
    autocomplete_fields = ["company"]
    essential = ["company", "title", "category", "target_date", "status"]


@admin.register(Interaction)
class InteractionAdmin(ModelAdmin):
    list_display = ["occurred_on", "company", "summary"]
    list_filter = ["kind"]
    search_fields = ["summary", "details", "company__name", "contact__full_name"]
    autocomplete_fields = ["company", "deal", "contact"]
    essential = ["occurred_on", "kind", "company", "summary"]


@admin.register(Document)
class DocumentAdmin(ModelAdmin):
    list_display = ["title", "company", "category", "document_date"]
    list_filter = ["category"]
    search_fields = ["title", "original_filename", "company__name"]
    autocomplete_fields = ["company", "deal"]
    readonly_fields = ["original_filename", "mime_type", "size_bytes", "sha256", "uploaded_at"]
    essential = ["title", "file", "company", "category", "document_date"]
    inlines = [DocumentLinkInline]


@admin.register(Contact)
class ContactAdmin(ModelAdmin):
    list_display = ["full_name", "company", "role"]
    search_fields = ["full_name", "email", "company__name"]
    autocomplete_fields = ["company", "co_investor"]
    essential = ["full_name", "company", "co_investor", "role", "email", "phone"]


@admin.register(CoInvestor)
class CoInvestorAdmin(ModelAdmin):
    list_display = ["name", "kind", "city"]
    list_filter = ["kind"]
    search_fields = ["name"]
    essential = ["name", "kind"]


@admin.register(RoundParticipant)
class RoundParticipantAdmin(ModelAdmin):
    list_display = ["deal", "co_investor", "role", "status", "amount", "currency"]
    list_filter = ["role", "status"]
    autocomplete_fields = ["deal", "co_investor", "contact"]
    essential = ["deal", "co_investor", "role", "status", "amount", "currency"]


@admin.register(InvestingEntity)
class InvestingEntityAdmin(ModelAdmin):
    list_display = ["name", "kind"]
    search_fields = ["name"]
    essential = ["name", "kind"]


@admin.register(FxRate)
class FxRateAdmin(ModelAdmin):
    list_display = ["rate_date", "currency", "chf_per_unit"]
    list_filter = ["currency"]
    search_fields = ["currency"]


@admin.register(ConvertibleTerms)
class ConvertibleTermsAdmin(ModelAdmin):
    list_display = ["investment", "valuation_cap", "maturity_date"]
    search_fields = ["investment__company__name"]
    autocomplete_fields = ["investment"]
    essential = ["investment", "valuation_cap", "discount_pct", "interest_rate", "maturity_date"]
