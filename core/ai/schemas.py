"""Shapes of the data Claude returns (structured outputs, validated by the SDK)."""

from typing import Literal

from pydantic import BaseModel

Confidence = Literal["high", "medium", "low"]
InvestorKind = Literal["vc", "strategic", "angel", "family_office", "public", "other"]
CompanyField = Literal["legal_name", "website", "sector", "city", "country", "description"]
DealField = Literal["round_name", "round_size", "pre_money", "proposed_ticket", "currency", "deadline", "next_step"]
DocumentCategory = Literal["pitch_deck", "term_sheet", "shareholders_agreement", "subscription", "board_minutes",
                           "board_material", "financials", "investor_report", "general_assembly", "cap_table",
                           "charter", "regulatory", "legal", "other"]


class CompanyFact(BaseModel):
    field: CompanyField
    value: str
    source_url: str | None
    evidence: str | None
    confidence: Confidence


class InvestorRef(BaseModel):
    name: str
    kind: InvestorKind


class PastRound(BaseModel):
    round_name: str
    announced_on: str | None          # YYYY-MM-DD, or YYYY-MM-01 when only the month is known
    amount: float | None
    currency: str | None              # ISO code, e.g. CHF, EUR, USD
    pre_money: float | None
    post_money: float | None
    lead_investors: list[InvestorRef]
    other_investors: list[InvestorRef]
    source_url: str | None
    confidence: Confidence


class Person(BaseModel):
    full_name: str
    role: str | None
    organization: str | None
    email: str | None
    phone: str | None
    profile_url: str | None
    source_url: str | None
    confidence: Confidence


class NewsItem(BaseModel):
    date: str | None                  # YYYY-MM-DD
    headline: str
    summary: str
    category: Literal["regulatory", "clinical", "commercial", "financing", "product", "people", "other"]
    source_url: str | None


class CompanyResearch(BaseModel):
    company_facts: list[CompanyFact]
    past_rounds: list[PastRound]
    people: list[Person]
    news: list[NewsItem]
    caveats: str | None


class DealFact(BaseModel):
    field: DealField
    value: str                        # numbers as plain digits, dates as YYYY-MM-DD
    evidence: str | None
    confidence: Confidence


class RoundInvestor(BaseModel):
    name: str
    kind: InvestorKind
    role: Literal["lead", "co_lead", "participant"]
    status: Literal["contacted", "interested", "committed", "signed", "declined"]
    amount: float | None
    currency: str | None
    evidence: str | None


class AttachmentInfo(BaseModel):
    filename: str
    category: DocumentCategory
    title: str
    document_date: str | None
    folder: str | None                # groups documents that belong together, e.g. "Series B", "Board 2026-03"


class EventInfo(BaseModel):
    kind: Literal["general_assembly", "board", "meeting"]
    date: str                         # YYYY-MM-DD
    time: str | None                  # HH:MM, 24 hours
    location: str | None              # city, address or video link
    title: str
    agenda: str | None                # short agenda, if given
    evidence: str | None


class ReportFigures(BaseModel):
    as_of: str                        # YYYY-MM-DD, end of the period the figures refer to
    currency: str | None
    cash: float | None
    monthly_burn: float | None
    revenue_ttm: float | None
    headcount: int | None
    evidence: str | None


class EmailExtraction(BaseModel):
    company_name: str | None
    company_website: str | None
    company_facts: list[CompanyFact]
    deal_facts: list[DealFact]
    round_investors: list[RoundInvestor]
    people: list[Person]
    key_terms: str | None
    attachments: list[AttachmentInfo]
    events: list[EventInfo]
    report_figures: list[ReportFigures]
    summary: str
