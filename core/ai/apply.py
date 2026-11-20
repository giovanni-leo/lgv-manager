"""Turn an accepted suggestion into real records."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import (CoInvestor, CompanyMetric, Contact, Interaction, RoundParticipant, Suggestion,
                         Valuation)

COMPANY_FIELDS = {"legal_name", "website", "sector", "city", "country", "description"}
DEAL_MONEY_FIELDS = {"round_size", "pre_money", "proposed_ticket"}
DEAL_FIELDS = DEAL_MONEY_FIELDS | {"round_name", "currency", "deadline", "next_step"}
INVESTOR_KINDS = {k for k, _label in CoInvestor.Kind.choices}


class SuggestionError(Exception):
    """The proposed value cannot be applied as it is."""


def parse_amount(value):
    if value in (None, ""):
        return None
    text = re.sub(r"[\s'’,]", "", str(value))
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise SuggestionError(f"'{value}' is not a number.") from None
    if amount < 0:
        raise SuggestionError("Amounts cannot be negative.")
    return amount.quantize(Decimal("0.01"))


def parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}", text):
        text += "-01"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        raise SuggestionError(f"'{value}' is not a date (YYYY-MM-DD).") from None


def parse_currency(value, default="CHF"):
    code = (value or default).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise SuggestionError(f"'{value}' is not a currency code.")
    if code not in settings.CURRENCIES:
        raise SuggestionError(f"{code} is not one of the currencies in use ({', '.join(settings.CURRENCIES)}).")
    return code


def get_or_create_investor(name, kind="other"):
    name = name.strip()
    investor = CoInvestor.objects.filter(name__iexact=name).first()
    if investor is None:
        investor = CoInvestor.objects.create(name=name, kind=kind if kind in INVESTOR_KINDS else "other")
    return investor


def _company_field(s):
    field, value = s.payload["field"], (s.payload.get("value") or "").strip()
    if field not in COMPANY_FIELDS:
        raise SuggestionError(f"Unknown company field {field}.")
    if field == "country":
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value):
            raise SuggestionError("The country must be a two-letter code, e.g. CH.")
    setattr(s.company, field, value or None)
    s.company.save(update_fields=[field, "updated_at"])


def _deal_field(s):
    if s.deal is None:
        raise SuggestionError("There is no deal to update.")
    field, value = s.payload["field"], s.payload.get("value")
    if field not in DEAL_FIELDS:
        raise SuggestionError(f"Unknown round field {field}.")
    if field in DEAL_MONEY_FIELDS:
        value = parse_amount(value)
    elif field == "deadline":
        value = parse_date(value)
    elif field == "currency":
        value = parse_currency(value)
    else:
        value = (value or "").strip() or None
    setattr(s.deal, field, value)
    s.deal.save(update_fields=[field, "updated_at"])


def _deal_note(s):
    if s.deal is None:
        raise SuggestionError("There is no deal to update.")
    heading = s.payload.get("heading") or "Key terms"
    text = f"{heading} ({timezone.localdate():%Y-%m-%d}):\n{s.payload['text'].strip()}"
    s.deal.notes = f"{s.deal.notes}\n\n{text}" if s.deal.notes else text
    s.deal.save(update_fields=["notes", "updated_at"])


def _participant(s):
    if s.deal is None:
        raise SuggestionError("There is no deal to add the investor to.")
    p = s.payload
    investor = get_or_create_investor(p["name"], p.get("kind", "other"))
    RoundParticipant.objects.update_or_create(
        deal=s.deal, co_investor=investor,
        defaults={"role": p.get("role") or "participant", "status": p.get("status") or "interested",
                  "amount": parse_amount(p.get("amount") or None),
                  "currency": parse_currency(p.get("currency"), s.deal.currency)})


def _contact(s):
    p = s.payload
    name = p["full_name"].strip()
    if p.get("co_investor"):
        investor = get_or_create_investor(p["co_investor"], p.get("investor_kind", "other"))
        contact = Contact.objects.filter(full_name__iexact=name, co_investor=investor).first() \
            or Contact(full_name=name, co_investor=investor)
    else:
        contact = Contact.objects.filter(full_name__iexact=name, company=s.company).first() \
            or Contact(full_name=name, company=s.company)
    for field in ("role", "email", "phone"):
        if p.get(field) and not getattr(contact, field):
            setattr(contact, field, p[field])
    if p.get("profile_url") and p["profile_url"] not in (contact.notes or ""):
        contact.notes = ((contact.notes or "") + f"\n{p['profile_url']}").strip()
    contact.save()


def _past_round(s):
    """Add the round to the company's funding history (and its valuation, when known)."""
    from core.models import FundingRound, FundingRoundInvestor

    from .funding import record_funding_round

    funding_round = record_funding_round(s.company, s.payload, FundingRound, FundingRoundInvestor, CoInvestor)
    if funding_round.post_money is not None and funding_round.currency in settings.CURRENCIES:
        when = funding_round.announced_on or timezone.localdate()
        Valuation.objects.get_or_create(
            company=s.company, valuation_date=when, method="round", post_money=funding_round.post_money,
            defaults={"currency": funding_round.currency,
                      "notes": f"{funding_round.round_name} (from public sources)"})


def _news(s):
    p = s.payload
    details = "\n".join(x for x in (p.get("summary"), f"Source: {p['source_url']}" if p.get("source_url") else None)
                        if x)
    Interaction.objects.create(company=s.company, occurred_on=parse_date(p.get("date")) or timezone.localdate(),
                               kind="news", summary=p["headline"], details=details or None)


EVENT_KINDS = {"general_assembly": "general_assembly", "board": "board", "meeting": "meeting"}
EVENT_NAMES = {"general_assembly": "General assembly", "board": "Board meeting", "meeting": "Meeting"}


def _whole_number(value):
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise SuggestionError(f"'{value}' is not a whole number.") from None


def _event(s):
    """An announced meeting becomes a dated entry in Activity and events (shown in Home while it is ahead)."""
    p = s.payload
    when = parse_date(p.get("date"))
    if when is None:
        raise SuggestionError("The event has no valid date.")
    kind = EVENT_KINDS.get(p.get("kind"), "meeting")
    if Interaction.objects.filter(company=s.company, kind=kind, occurred_on=when).exists():
        return  # already recorded
    place = ", ".join(x for x in (p.get("time"), p.get("location")) if x)
    title = p.get("title") or EVENT_NAMES[kind]
    details = "\n".join(x for x in (f"Agenda: {p['agenda']}" if p.get("agenda") else None,
                                    f"Source: {s.source_url}" if s.source_url else None) if x)
    Interaction.objects.create(company=s.company, occurred_on=when, kind=kind,
                               summary=f"{title}{' – ' + place if place else ''}"[:500], details=details or None)


def _figures(s):
    """Report figures fill the company's figures for that date; values already there are kept."""
    p = s.payload
    when = parse_date(p.get("as_of"))
    if when is None:
        raise SuggestionError("The figures have no valid date.")
    metric, _created = CompanyMetric.objects.get_or_create(
        company=s.company, as_of=when, defaults={"currency": parse_currency(p.get("currency"), "CHF")})
    for field, parser in (("cash", parse_amount), ("monthly_burn", parse_amount), ("revenue_ttm", parse_amount),
                          ("headcount", _whole_number)):
        value = parser(p.get(field) or None)             # 0 and "" mean "not stated"
        if value is not None and getattr(metric, field) is None:
            setattr(metric, field, value)
    metric.save()


def _cap_table(s):
    from core.services.cap_snapshot import apply_snapshot

    apply_snapshot(s.company, s.payload)


HANDLERS = {
    Suggestion.Target.COMPANY_FIELD: _company_field,
    Suggestion.Target.DEAL_FIELD: _deal_field,
    Suggestion.Target.DEAL_NOTE: _deal_note,
    Suggestion.Target.PARTICIPANT: _participant,
    Suggestion.Target.CONTACT: _contact,
    Suggestion.Target.PAST_ROUND: _past_round,
    Suggestion.Target.NEWS: _news,
    Suggestion.Target.EVENT: _event,
    Suggestion.Target.FIGURES: _figures,
    Suggestion.Target.CAP_TABLE: _cap_table,
}


def decide(suggestion, user, accept):
    """Accept (apply) or reject a pending suggestion. Raises SuggestionError if it cannot be applied."""
    with transaction.atomic():
        s = Suggestion.objects.select_for_update(of=("self",)).select_related("company", "deal").get(pk=suggestion.pk)
        if s.status != Suggestion.Status.PENDING:
            return s
        if accept:
            HANDLERS[s.target](s)
        s.status = Suggestion.Status.ACCEPTED if accept else Suggestion.Status.REJECTED
        s.decided_by, s.decided_at = user, timezone.now()
        s.save(update_fields=["status", "decided_by", "decided_at"])
        return s
