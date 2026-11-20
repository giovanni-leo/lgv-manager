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


# The models below are used for emails. They have no optional fields on purpose: every ``X | None`` adds to the
# grammar that structured outputs compile, and the API refuses a schema that is too large. An unknown text is "",
# an unknown number is 0.

class EmailFact(BaseModel):
    field: CompanyField
    value: str
    evidence: str                     # "" if none
    confidence: Confidence


class DealFact(BaseModel):
    field: DealField
    value: str                        # numbers as plain digits, dates as YYYY-MM-DD
    evidence: str                     # "" if none
    confidence: Confidence


class EmailPerson(BaseModel):
    full_name: str
    role: str                         # "" if not stated
    organization: str
    email: str
    phone: str


class RoundInvestor(BaseModel):
    name: str
    kind: InvestorKind
    role: Literal["lead", "co_lead", "participant"]
    status: Literal["contacted", "interested", "committed", "signed", "declined"]
    amount: float                     # 0 if not stated
    currency: str                     # "" if not stated


class AttachmentInfo(BaseModel):
    filename: str
    category: DocumentCategory
    title: str
    document_date: str                # YYYY-MM-DD, "" if unknown
    folder: str                       # groups documents that belong together, e.g. "Series B"; "" if alone


class EventInfo(BaseModel):
    kind: Literal["general_assembly", "board", "meeting"]
    date: str                         # YYYY-MM-DD
    time: str                         # HH:MM, "" if not stated
    location: str                     # city, address or video link; "" if not stated
    title: str
    agenda: str                       # short agenda, "" if none


class ReportFigures(BaseModel):
    as_of: str                        # YYYY-MM-DD, end of the period the figures refer to
    currency: str                     # "" if not stated
    cash: float                       # 0 if not stated
    monthly_burn: float
    revenue_ttm: float
    headcount: int


class EmailExtraction(BaseModel):
    company_name: str                 # "" if the email does not say
    company_website: str
    company_facts: list[EmailFact]
    deal_facts: list[DealFact]
    round_investors: list[RoundInvestor]
    people: list[EmailPerson]
    key_terms: str                    # "" if none
    attachments: list[AttachmentInfo]
    events: list[EventInfo]
    report_figures: list[ReportFigures]
    summary: str


# --- Capitalization read from financing documents ---------------------------------------------

class ClassSnapshot(BaseModel):
    name: str                          # as in the documents, e.g. "Common", "Series A-1 Preferred"
    is_common: bool
    shares_outstanding: int            # issued and outstanding right after the closing
    original_issue_price: float        # 0 if not stated
    liquidation_multiple: float        # 1 = one times the original issue price; 0 for common
    participating: bool
    seniority_rank: int                # 1 = paid first in a liquidation; equal numbers = pari passu; 0 for common
    conversion_ratio: float            # common shares per share of this class, usually 1


class HoldingSnapshot(BaseModel):
    investor_name: str                 # as written in the documents
    class_name: str
    shares: int
    price_per_share: float             # 0 if not stated
    amount: float                      # 0 if not stated


class CapTableSnapshot(BaseModel):
    round_name: str                    # e.g. "Series B"; "" if unknown
    closing_date: str                  # YYYY-MM-DD; "" if unknown
    currency: str
    classes: list[ClassSnapshot]
    option_pool_total: int             # options granted plus shares still available for grant, not exercised; 0 if unknown
    option_pool_granted: int           # 0 if unknown
    warrants: int                      # 0 if none or unknown
    fully_diluted_total: int           # as stated in the documents; 0 if not stated
    round_class_name: str              # the class sold in this round; "" if unknown
    round_price_per_share: float       # 0 if unknown
    round_amount_raised: float         # 0 if unknown
    pre_money: float                   # 0 if not stated
    post_money: float                  # 0 if not stated
    lead_investors: list[InvestorRef]
    other_investors: list[InvestorRef]
    our_holdings: list[HoldingSnapshot]  # only purchasers that are one of our investing entities
    evidence: str                      # which documents and sections the numbers come from
    caveats: str                       # conflicts between documents, missing data; "" if none


class CapTableReading(BaseModel):
    snapshot: CapTableSnapshot | None  # null when the documents do not describe a capitalization
    note: str                          # "" if nothing to add
