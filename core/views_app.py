"""At-a-glance pages: pipeline, deal, portfolio and company."""

from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (FUNNEL_STAGES, CoInvestor, Company, Contact, Deal, DealStage, Document, FundingRound,
                     InvestingEntity,
                     Interaction, RoundParticipant)
from .models.pipeline import FUND_REQUIRED_STAGES
from .funds import active_fund, fund_company_ids
from .sections import SECTIONS, formfield
from .services.exit import exit_analysis
from .services.people import sort_people
from .services.ownership import consistency_warnings
from .services.portfolio import (company_summary, deal_summary, known_investors, portfolio_totals,
                                 report_documents, runway, timeline)
from .widgets import IsoDateInput

OPEN_STAGES = [s.value for s in FUNNEL_STAGES if s != DealStage.INVESTED]


def _require(request, perm):
    if not request.user.has_perm(f"core.{perm}"):
        raise PermissionDenied


# --- Quick-add forms ----------------------------------------------------------

class InteractionForm(forms.ModelForm):
    class Meta:
        model = Interaction
        fields = ["occurred_on", "kind", "summary"]
        formfield_callback = formfield

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["summary"].widget.attrs["placeholder"] = "What happened?"


class ParticipantForm(forms.Form):
    investor = forms.CharField(max_length=200, widget=forms.TextInput(
        attrs={"list": "co-investor-names", "placeholder": "Investor name", "autocomplete": "off"}))
    kind = forms.ChoiceField(choices=CoInvestor.Kind.choices, initial=CoInvestor.Kind.VC)
    role = forms.ChoiceField(choices=RoundParticipant.Role.choices, initial=RoundParticipant.Role.PARTICIPANT)
    status = forms.ChoiceField(choices=RoundParticipant.Status.choices, initial=RoundParticipant.Status.INTERESTED)
    amount = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2,
                                widget=forms.NumberInput(attrs={"step": "any", "inputmode": "decimal",
                                                                "placeholder": "Amount"}))


class NewDealForm(forms.Form):
    company = forms.ModelChoiceField(queryset=Company.objects.all(), required=False)
    new_company = forms.CharField(required=False, max_length=200)
    sector = forms.CharField(required=False, max_length=200)
    round_name = forms.CharField(required=False, max_length=100)
    round_size = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2)
    pre_money = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2)
    proposed_ticket = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2)
    currency = forms.ChoiceField(choices=Deal._meta.get_field("currency").choices, initial="CHF")
    deadline = forms.DateField(required=False, widget=IsoDateInput())
    source_contact = forms.ModelChoiceField(queryset=Contact.objects.all(), required=False)
    fund = forms.ModelChoiceField(queryset=InvestingEntity.objects.all(), required=False,
                                  help_text="Needed from the Qualified stage on.")

    def clean(self):
        data = super().clean()
        if not data.get("company") and not data.get("new_company"):
            raise forms.ValidationError("Choose a company or type the name of a new one.")
        return data


# --- Context builders -----------------------------------------------------------

def _fund_positions(company):
    """The position of each fund in the company, in the currency of the fund."""
    funds = InvestingEntity.objects.filter(Q(investments__company=company) | Q(realizations__company=company)).distinct()
    return [company_summary(company, fund=f) for f in funds]


def _folders(company):
    """The company's document folders, each with its documents (newest first)."""
    docs = Prefetch("documents", queryset=Document.objects.order_by("-document_date", "-uploaded_at"))
    return list(company.document_folders.prefetch_related(docs))


def _deal_contacts(deal, participants):
    seen, rows = set(), []

    def add(contact, label):
        if contact and contact.pk not in seen:
            seen.add(contact.pk)
            rows.append({"contact": contact, "label": label})

    for c in sort_people(deal.company.contacts.all()):
        add(c, deal.company.name)
    add(deal.source_contact, "Source")
    for p in participants:
        add(p.contact, p.co_investor.name)
        for c in p.co_investor.contacts.all():
            add(c, p.co_investor.name)
    return rows


def deal_context(request, deal):
    from .views_ai import suggestions_context

    ds = deal_summary(deal)
    return {**suggestions_context(deal.company, deal),
        "nav_section": "pipeline",
        "deal": deal, "company": deal.company, "ds": ds,
        "stages": DealStage.choices,
        "contacts": _deal_contacts(deal, ds.participants),
        "funding_rounds": deal.company.funding_rounds.prefetch_related("investors__co_investor"),
        "existing_investors": [k for k in known_investors(deal.company)
                               if k.investor.pk not in {p.co_investor_id for p in ds.participants}],
        "events": timeline(deal=deal),
        "documents": Document.objects.filter(Q(deal=deal) | Q(company=deal.company), folder__isnull=True).order_by(
            "-document_date", "-uploaded_at")[:15],
        "document_folders": _folders(deal.company),
        "documents_count": deal.company.documents.count(),
        "history": deal.stage_history.all(),
        "participant_form": ParticipantForm(),
        "co_investor_names": CoInvestor.objects.values_list("name", flat=True),
        "interaction_form": InteractionForm(initial={"occurred_on": timezone.localdate(), "kind": "call"}),
        "interaction_target": {"deal": deal.pk, "company": deal.company_id},
    }


def _next_event(company, summary):
    """The next dated thing to prepare for: a general assembly or a planned milestone."""
    today = timezone.localdate()
    candidates = []
    if summary.next_assembly:
        candidates.append({"date": summary.next_assembly, "title": "General assembly"})
    milestone = (company.milestones.filter(status__in=["planned", "delayed"], target_date__gte=today)
                 .order_by("target_date").first())
    if milestone:
        candidates.append({"date": milestone.target_date, "title": milestone.title})
    return min(candidates, key=lambda c: c["date"]) if candidates else None


def company_context(request, company):
    from .views_ai import suggestions_context

    summary = company_summary(company)
    funding_rounds = list(company.funding_rounds.prefetch_related("investors__co_investor"))
    show_portfolio = company.status != Company.Status.PROSPECT or company.investments.exists()
    return {**suggestions_context(company),
        "exit": exit_analysis(company, request.GET.get("exit")) if show_portfolio else None,
        "show_portfolio": show_portfolio,
        "fund_positions": _fund_positions(company),
        "nav_section": "portfolio" if show_portfolio else "pipeline",
        "latest_round": funding_rounds[0] if funding_rounds else None,
        "next_event": _next_event(company, summary),
        "documents_count": company.documents.count(),
        "company": company,
        "s": summary,
        "open_deals": company.deals.filter(stage__in=OPEN_STAGES + ["on_hold"]),
        "investments": company.investments.select_related("share_class").order_by("-invest_date"),
        "realizations": company.realizations.order_by("-realized_on"),
        "events": timeline(company=company),
        "reports": report_documents(company),
        "contacts": [{"contact": c, "label": ""} for c in sort_people(company.contacts.all())],
        "funding_rounds": funding_rounds,
        "investors": known_investors(company),
        "documents": company.documents.filter(folder__isnull=True).order_by("-document_date", "-uploaded_at")[:15],
        "document_folders": _folders(company),
        "metrics": company.metrics.order_by("-as_of")[:4],
        "interaction_form": InteractionForm(initial={"occurred_on": timezone.localdate(), "kind": "note"}),
        "interaction_target": {"company": company.pk},
    }


def _section_context(request, kind, obj):
    if kind == "deal":
        return deal_context(request, obj)
    if kind == "company":
        return company_context(request, obj)
    if kind == "participant":
        return {"p": obj, "deal": obj.deal}
    raise Http404


# --- Pages ----------------------------------------------------------------------

@permission_required("core.view_deal", raise_exception=True)
def pipeline(request):
    deals = Deal.objects.select_related("company", "fund").prefetch_related("participants__co_investor")
    fund = active_fund(request)
    if fund:                          # deals of the fund, and the new ones that have no fund yet
        deals = deals.filter(Q(fund=fund) | Q(fund__isnull=True))
    deals = list(deals.order_by("deadline", "next_step_due", "-opened_on"))
    columns = [{"stage": s, "deals": [d for d in deals if d.stage == s.value]} for s in FUNNEL_STAGES]
    parked = [{"stage": s, "deals": [d for d in deals if d.stage == s.value]}
              for s in (DealStage.ON_HOLD, DealStage.PASSED)]
    for col in columns + parked:
        for d in col["deals"]:
            d.lead = next((p.co_investor for p in d.participants.all() if p.role == "lead"), None)
    return render(request, "app/pipeline.html", {"columns": columns, "parked": parked})


@permission_required("core.view_deal", raise_exception=True)
def deal_detail(request, pk):
    deal = get_object_or_404(Deal.objects.select_related("company", "source_contact"), pk=pk)
    return render(request, "app/deal/detail.html", deal_context(request, deal))


@permission_required("core.add_deal", raise_exception=True)
def deal_new(request):
    form = NewDealForm(request.POST or None, initial={"company": request.GET.get("company")})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            company = data["company"] or Company.objects.create(
                name=data["new_company"].strip(), sector=data["sector"] or None)
            deal = Deal.objects.create(
                company=company, round_name=data["round_name"] or None, round_size=data["round_size"],
                pre_money=data["pre_money"], proposed_ticket=data["proposed_ticket"], currency=data["currency"],
                deadline=data["deadline"], source_contact=data["source_contact"], fund=data["fund"])
        return redirect("deal_detail", pk=deal.pk)
    return render(request, "app/deal/new.html", {"form": form, "ai_enabled": settings.AI_ENABLED})


@require_POST
@permission_required("core.change_deal", raise_exception=True)
def deal_stage(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    stage = request.POST.get("stage")
    if stage not in DealStage.values:
        return HttpResponse(status=400)
    if stage != deal.stage:
        if stage in FUND_REQUIRED_STAGES and deal.fund_id is None:
            funds = list(InvestingEntity.objects.all()[:2])
            if len(funds) == 1:
                deal.fund = funds[0]                      # the only fund: nothing to choose
            elif funds:
                messages.error(request, "Choose the fund of this deal (Round card › Edit) before moving it to "
                                        f"{DealStage(stage).label}.")
                response = HttpResponse(status=204)
                response["HX-Refresh"] = "true"
                return response
        deal.stage = stage
        if stage in (DealStage.INVESTED, DealStage.PASSED) and not deal.closed_on:
            deal.closed_on = timezone.localdate()
        if stage == DealStage.INVESTED and deal.company.status == Company.Status.PROSPECT:
            deal.company.status = Company.Status.PORTFOLIO
            deal.company.save(update_fields=["status", "updated_at"])
        deal.save()
    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response


@require_POST
@permission_required("core.add_roundparticipant", raise_exception=True)
def participant_add(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    form = ParticipantForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        name = data["investor"].strip()
        investor = CoInvestor.objects.filter(name__iexact=name).first()
        if investor is None:
            _require(request, "add_coinvestor")
            investor = CoInvestor.objects.create(name=name, kind=data["kind"])
        RoundParticipant.objects.update_or_create(
            deal=deal, co_investor=investor,
            defaults={"role": data["role"], "status": data["status"], "amount": data["amount"],
                      "currency": deal.currency})
        form = ParticipantForm()
    ctx = deal_context(request, deal)
    ctx["participant_form"] = form
    return render(request, "app/deal/_participants.html", ctx)


@require_POST
@permission_required("core.delete_roundparticipant", raise_exception=True)
def participant_delete(request, pk):
    participant = get_object_or_404(RoundParticipant, pk=pk)
    deal = participant.deal
    participant.delete()
    return render(request, "app/deal/_participants.html", deal_context(request, deal))


@require_POST
@permission_required("core.add_interaction", raise_exception=True)
def interaction_add(request):
    company = get_object_or_404(Company, pk=request.POST.get("company"))
    deal = get_object_or_404(Deal, pk=request.POST["deal"], company=company) if request.POST.get("deal") else None
    form = InteractionForm(request.POST)
    if form.is_valid():
        interaction = form.save(commit=False)
        interaction.company, interaction.deal = company, deal
        interaction.save()
        form = None
    ctx = deal_context(request, deal) if deal else company_context(request, company)
    if form is not None:
        ctx["interaction_form"] = form
    return render(request, "app/_events.html", ctx)


@permission_required("core.view_company", raise_exception=True)
def portfolio(request):
    show = request.GET.get("show", "active")
    statuses = ["portfolio"] if show == "active" else ["portfolio", "exited", "written_off"]
    selected = active_fund(request)
    funds = [selected] if selected else list(InvestingEntity.objects.all())
    sections = []
    for fund in funds:                # one section per fund: each in its own currency, never mixed
        companies = (Company.objects.filter(status__in=statuses)
                     .filter(Q(investments__entity=fund) | Q(realizations__entity=fund)).distinct().order_by("name"))
        summaries = [company_summary(c, fund=fund) for c in companies]
        if summaries or selected:
            sections.append({"fund": fund, "summaries": summaries, "totals": portfolio_totals(summaries),
                             "ccy": fund.base_currency})
    if not funds:                     # no fund set up yet: everything in CHF, as before
        summaries = [company_summary(c) for c in Company.objects.filter(status__in=statuses).order_by("name")]
        sections.append({"fund": None, "summaries": summaries, "totals": portfolio_totals(summaries), "ccy": "CHF"})
    return render(request, "app/portfolio.html", {"sections": sections, "show": show})


@permission_required("core.view_company", raise_exception=True)
def company_detail(request, pk):
    company = get_object_or_404(Company, pk=pk)
    return render(request, "app/company/detail.html", company_context(request, company))


def section(request, kind, pk, name):
    """Show a section, its edit form (?edit), or save it (POST)."""
    sec = SECTIONS.get((kind, name))
    if sec is None:
        raise Http404
    obj = get_object_or_404(sec.model, pk=pk)
    _require(request, f"view_{sec.model._meta.model_name}")
    url = reverse("section", args=[kind, pk, name])
    form_class = sec.form_class()
    if request.method == "POST" or "edit" in request.GET:
        _require(request, sec.perm)
        form = form_class(request.POST or None, instance=obj)
        if request.method == "POST" and form.is_valid():
            form.save()
            obj.refresh_from_db()
            if kind == "participant":
                response = render(request, "app/deal/_participants.html", deal_context(request, obj.deal))
                response["HX-Retarget"] = f"#deal-{obj.deal_id}-participants"
                response["HX-Reswap"] = "outerHTML"
                return response
        else:
            return render(request, "app/_section_form.html", {"form": form, "url": url, "row": sec.row, "wide": sec.wide,
                                                              "section_id": f"{kind}-{pk}-{name}"})
    return render(request, sec.template, _section_context(request, kind, obj))


def _named(warnings):
    names = dict(Company.objects.filter(pk__in={w.company_id for w in warnings}).values_list("pk", "name"))
    return [{**w.__dict__, "company_name": names.get(w.company_id, "")} for w in warnings]


def home(request):
    today = timezone.localdate()
    soon = today + timedelta(days=14)
    can_deals = request.user.has_perm("core.view_deal")
    can_companies = request.user.has_perm("core.view_company")
    fund = active_fund(request)
    company_ids = set(fund_company_ids(fund)) if fund else None
    deadlines = []
    if can_deals:
        open_deals = Deal.objects.filter(stage__in=OPEN_STAGES).select_related("company", "fund")
        if fund:                                  # the fund's deals and the new ones that have no fund yet
            open_deals = open_deals.filter(Q(fund=fund) | Q(fund__isnull=True))
        for d in open_deals:
            for label, when in (("Round deadline", d.deadline), ("Next step", d.next_step_due)):
                if when and when <= soon:
                    deadlines.append({"deal": d, "label": label, "date": when,
                                      "text": d.next_step if label == "Next step" else d.round_name})
        deadlines.sort(key=lambda x: x["date"])
    alerts, assemblies = [], []
    if can_companies:
        portfolio_companies = Company.objects.filter(status="portfolio")
        if fund:
            portfolio_companies = portfolio_companies.filter(pk__in=company_ids)
        for c in portfolio_companies:
            r = runway(c)
            if r and r.level in ("warning", "critical"):
                alerts.append({"company": c, "runway": r})
        meetings = Interaction.objects.filter(kind__in=["general_assembly", "board", "meeting"],
                                              occurred_on__gte=today).select_related("company")
        assemblies = (meetings.filter(company_id__in=company_ids) if fund else meetings).order_by("occurred_on")[:10]
    rounds = FundingRound.objects.filter(announced_on__isnull=False).exclude(company__status__in=["archived"])
    recent = Interaction.objects.filter(occurred_on__lte=today)
    warnings = _named(consistency_warnings()) if can_companies else []
    if fund:
        rounds, recent = rounds.filter(company_id__in=company_ids), recent.filter(company_id__in=company_ids)
        warnings = [w for w in warnings if w["company_id"] in company_ids]
    return render(request, "core/home.html", {
        "deadlines": deadlines, "alerts": alerts, "assemblies": assemblies, "warnings": warnings,
        "recent_rounds": rounds.select_related("company").prefetch_related("investors__co_investor")
        .order_by("-announced_on", "-id")[:8] if can_companies else [],
        "recent": recent.select_related("company", "deal").order_by("-occurred_on", "-id")[:8]
        if can_companies else [],
        "fund": fund,
    })
