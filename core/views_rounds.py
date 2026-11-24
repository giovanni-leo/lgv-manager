"""Page with the one-step form that records a financing round."""

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Company, Deal, InvestingEntity, ShareEvent
from .services.rounds import ExistingClass, RoundInput, record_round
from .templatetags.vc import money, num
from .widgets import IsoDateInput

EXISTING_ROWS = 3
MONEY = {"max_digits": 18, "decimal_places": 2, "required": False, "min_value": 0}


def _number(**attrs):
    return forms.NumberInput(attrs={"step": "any", "inputmode": "decimal", **attrs})


class RoundForm(forms.Form):
    round_name = forms.CharField(label="Round", max_length=100,
                                 widget=forms.TextInput(attrs={"placeholder": "e.g. Series A"}))
    round_date = forms.DateField(label="Closing date", widget=IsoDateInput())
    share_class = forms.ModelChoiceField(label="Share class of the round", queryset=None, required=False,
                                         empty_label="— new class —")
    new_class_name = forms.CharField(label="New class name", required=False, max_length=100,
                                     widget=forms.TextInput(attrs={"placeholder": "e.g. Series A preferred"}))
    price_per_share = forms.DecimalField(label="Price per share", max_digits=18, decimal_places=6, required=False,
                                         min_value=0, widget=_number(id="price"))
    new_shares = forms.IntegerField(label="New shares issued (all investors)", min_value=1,
                                    widget=_number(id="new_shares", step="1"))
    currency = forms.ChoiceField(label="Currency", choices=[("CHF", "CHF"), ("EUR", "EUR"), ("USD", "USD")])
    pre_money = forms.DecimalField(label="Pre-money valuation (if known)", widget=_number(), **MONEY)
    our_shares = forms.IntegerField(label="Our shares", required=False, min_value=1,
                                    widget=_number(id="our_shares", step="1"))
    our_amount = forms.DecimalField(label="…or amount we invested", widget=_number(id="our_amount"), **MONEY)
    entity = forms.ModelChoiceField(label="Fund", queryset=InvestingEntity.objects.all(),
                                    required=False)
    pool_size = forms.IntegerField(label="Option pool after the round (granted + ungranted)", required=False,
                                   min_value=0, widget=_number(step="1"))
    warrants = forms.IntegerField(label="Warrants outstanding", required=False, min_value=0,
                                  widget=_number(step="1"))
    lead_investors = forms.CharField(label="Lead investor(s)", required=False,
                                     widget=forms.TextInput(attrs={"placeholder": "comma separated"}))
    other_investors = forms.CharField(label="Other investors", required=False,
                                      widget=forms.TextInput(attrs={"placeholder": "comma separated"}))
    notes = forms.CharField(label="Notes", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, company, first_time, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["share_class"].queryset = company.share_classes.all()
        self.first_time = first_time
        if first_time:
            for i in range(1, EXISTING_ROWS + 1):
                self.fields[f"ex_name_{i}"] = forms.CharField(label="Class", required=False, max_length=100)
                self.fields[f"ex_shares_{i}"] = forms.IntegerField(label="Shares outstanding", required=False,
                                                                   min_value=1, widget=_number(step="1"))
                self.fields[f"ex_common_{i}"] = forms.BooleanField(label="Common", required=False)

    def existing_rows(self):
        return [(self[f"ex_name_{i}"], self[f"ex_shares_{i}"], self[f"ex_common_{i}"])
                for i in range(1, EXISTING_ROWS + 1)] if self.first_time else []

    @staticmethod
    def names(value):
        return [n.strip() for n in (value or "").split(",") if n.strip()]


def record_round_view(request, company_pk):
    company = get_object_or_404(Company, pk=company_pk)
    if not (request.user.has_perm("core.add_investment") and request.user.has_perm("core.change_company")):
        raise PermissionDenied
    deal = None
    if request.GET.get("deal"):
        deal = get_object_or_404(Deal, pk=request.GET["deal"], company=company)
    first_time = not ShareEvent.objects.filter(company=company).exists()
    entities = list(InvestingEntity.objects.all()[:2])
    initial = {"round_date": timezone.localdate(), "currency": "CHF",
               "entity": entities[0] if len(entities) == 1 else None,
               "ex_name_1": "Common", "ex_common_1": True}
    if deal:
        if deal.fund_id:
            initial["entity"] = deal.fund
        initial.update({"round_name": deal.round_name or "", "currency": deal.currency,
                        "pre_money": deal.pre_money, "our_amount": deal.proposed_ticket,
                        "round_date": deal.closed_on or timezone.localdate()})
        lead = deal.participants.filter(role="lead").select_related("co_investor").first()
        if lead:
            initial["lead_investors"] = lead.co_investor.name
        others = deal.participants.exclude(role="lead").filter(status__in=["committed", "signed"])
        initial["other_investors"] = ", ".join(p.co_investor.name for p in others.select_related("co_investor"))

    form = RoundForm(request.POST or None, initial=initial, company=company, first_time=first_time)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if deal is None:  # an open deal for this same round is the one being closed
            deal = company.deals.filter(round_name__iexact=d["round_name"].strip()).exclude(
                stage__in=["invested", "passed"]).order_by("-opened_on", "-id").first()
        data = RoundInput(
            round_name=d["round_name"], round_date=d["round_date"], new_shares=d["new_shares"],
            share_class=d["share_class"], new_class_name=d["new_class_name"] or "",
            price_per_share=d["price_per_share"], currency=d["currency"], pre_money=d["pre_money"],
            our_shares=d["our_shares"], our_amount=d["our_amount"],
            entity=d["entity"] or (entities[0] if len(entities) == 1 else None),
            pool_size=d["pool_size"], warrants=d["warrants"],
            lead_investors=form.names(d["lead_investors"]), other_investors=form.names(d["other_investors"]),
            existing=[ExistingClass(d.get(f"ex_name_{i}") or "", d.get(f"ex_shares_{i}") or 0,
                                    bool(d.get(f"ex_common_{i}"))) for i in range(1, EXISTING_ROWS + 1)]
            if first_time else [],
            deal=deal, notes=d["notes"])
        try:
            result = record_round(company, data)
        except ValidationError as exc:
            for name, errors in exc.message_dict.items():
                form.add_error(name if name in form.fields else None, errors)
        else:
            parts = [f"{data.round_name} recorded: {num(data.new_shares)} new {result.share_class.name} shares"]
            if result.amount_raised is not None:
                parts.append(f"{money(result.amount_raised, data.currency)} raised")
            if result.post_money is not None:
                parts.append(f"post-money {money(result.post_money, data.currency)}")
            if result.investment:
                parts.append(f"our investment {num(result.investment.shares)} shares, "
                             f"{money(result.investment.amount, data.currency)}")
            messages.success(request, ", ".join(parts) + ".")
            return redirect("company_detail", pk=company.pk)
    return render(request, "app/company/round_form.html", {
        "company": company, "deal": deal, "form": form, "first_time": first_time,
        "single_entity": len(entities) == 1,
        "nav_section": "portfolio" if company.status != "prospect" else "pipeline",
    })
