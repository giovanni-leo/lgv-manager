"""Background jobs: web research and email analysis, turned into suggestions."""

import hashlib
import logging
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (AiJob, Company, Contact, Deal, DealStage, Document, DocumentFolder, InboundEmail,
                         Interaction, Suggestion)

from . import claude, mail
from .apply import SuggestionError, parse_date

log = logging.getLogger(__name__)
OPEN_STAGES = ["sourced", "first_meeting", "analysis", "due_diligence", "term_sheet", "on_hold"]
LEGAL_SUFFIXES = r"\b(sa|ag|gmbh|sàrl|sarl|ltd|limited|inc|llc|bv|nv|sas|srl|spa|plc|s\.a\.|a\.g\.)\b"
FIELD_LABELS = {"legal_name": "Legal name", "website": "Website", "sector": "Sector", "city": "City",
                "country": "Country", "description": "Description", "round_name": "Round",
                "round_size": "Round size", "pre_money": "Pre-money valuation",
                "proposed_ticket": "Ticket proposed to us", "currency": "Currency", "deadline": "Round deadline",
                "next_step": "Next step"}


# --- Queueing --------------------------------------------------------------------------

def queue_research(company, deal=None, user=None):
    """Queue a web search for a company, unless one is already waiting or running."""
    active = company.ai_jobs.filter(kind=AiJob.Kind.WEB_RESEARCH, status__in=["queued", "running"]).first()
    return active or AiJob.objects.create(kind=AiJob.Kind.WEB_RESEARCH, company=company, deal=deal,
                                          requested_by=user)


def queue_cap_table(company, document_ids, user=None):
    """Queue the reading of financing documents into a cap table proposal."""
    active = company.ai_jobs.filter(kind=AiJob.Kind.CAP_TABLE, status__in=["queued", "running"]).first()
    return active or AiJob.objects.create(kind=AiJob.Kind.CAP_TABLE, company=company, requested_by=user,
                                          params={"documents": list(document_ids)})


def claim_next_job():
    with transaction.atomic():
        job = (AiJob.objects.select_for_update(skip_locked=True).filter(status=AiJob.Status.QUEUED)
               .order_by("created_at").first())
        if job:
            job.status, job.started_at = AiJob.Status.RUNNING, timezone.now()
            job.save(update_fields=["status", "started_at"])
        return job


def fail_stale_jobs(minutes=45):
    """Jobs left running by a worker that stopped."""
    cutoff = timezone.now() - timedelta(minutes=minutes)
    return AiJob.objects.filter(status=AiJob.Status.RUNNING, started_at__lt=cutoff).update(
        status=AiJob.Status.FAILED, finished_at=timezone.now(), error="Interrupted (worker restarted).")


def run_job(job):
    try:
        if job.kind == AiJob.Kind.WEB_RESEARCH:
            run_research(job)
        elif job.kind == AiJob.Kind.EMAIL:
            run_email(job)
        elif job.kind == AiJob.Kind.CAP_TABLE:
            run_cap_table(job)
        job.status = AiJob.Status.DONE
    except Exception as exc:  # the job records the failure; the worker keeps running
        log.exception("Job %s failed", job.pk)
        job.status, job.error = AiJob.Status.FAILED, _explain(exc)[:2000]
        if job.inbound_email_id:
            InboundEmail.objects.filter(pk=job.inbound_email_id).update(status=InboundEmail.Status.FAILED,
                                                                        note=job.error)
    job.finished_at = timezone.now()
    job.save()
    return job


def _explain(exc):
    """A message a person can act on."""
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return "The Anthropic API key is not valid: check ANTHROPIC_API_KEY in the .env file."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "The Anthropic API key is not allowed to use this model or tool (check the account settings)."
    if isinstance(exc, anthropic.RateLimitError):
        return "Too many requests to Claude right now, or the account has run out of credit. Try again later."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Could not reach the Anthropic API (network). Try again later."
    return f"{type(exc).__name__}: {exc}"


def _record_usage(job, usage):
    job.input_tokens += usage.input_tokens
    job.output_tokens += usage.output_tokens
    job.web_searches += usage.web_searches


# --- Building suggestions -------------------------------------------------------------------

def _norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _suggest(job, company, deal, target, payload, label, source_url=None, evidence=None, confidence=None):
    """Create a pending suggestion unless the same one is already pending."""
    if Suggestion.objects.filter(company=company, target=target, label=label, status="pending").exists():
        return None
    return Suggestion.objects.create(job=job, company=company, deal=deal, target=target, payload=payload,
                                     label=label[:500], source_url=source_url, evidence=evidence,
                                     confidence=confidence)


def _fmt_amount(amount, currency):
    if amount is None:
        return ""
    for size, unit in ((1e9, "bn"), (1e6, "M"), (1e3, "k")):
        if amount >= size:
            return f"{currency or ''} {amount / size:.3g}{unit}".strip()
    return f"{currency or ''} {amount:,.0f}".strip()


def _company_facts(job, company, deal, facts):
    for f in facts:
        current = getattr(company, f.field, None)
        if _norm(current) == _norm(f.value) or not f.value.strip():
            continue
        was = f" (now: {current})" if current else ""
        _suggest(job, company, deal, Suggestion.Target.COMPANY_FIELD, {"field": f.field, "value": f.value},
                 f"{FIELD_LABELS[f.field]}: {f.value}{was}", getattr(f, "source_url", None) or None,
                 f.evidence or None, f.confidence)


def _people(job, company, deal, people, investor_names=()):
    existing = {_norm(n) for n in Contact.objects.filter(Q(company=company) | Q(co_investor__name__in=investor_names))
                .values_list("full_name", flat=True)}
    by_investor = {_norm(n): n for n in investor_names}
    for p in people:
        if _norm(p.full_name) in existing:
            continue
        org = by_investor.get(_norm(p.organization))
        payload = {"full_name": p.full_name, "role": p.role, "email": p.email, "phone": p.phone,
                   "profile_url": getattr(p, "profile_url", None), "co_investor": org}
        where = p.organization or company.name
        _suggest(job, company, deal, Suggestion.Target.CONTACT, payload,
                 f"{p.full_name} – {p.role or 'role unknown'} ({where})", getattr(p, "source_url", None), None,
                 getattr(p, "confidence", None))


def suggestions_from_research(job, company, research):
    deal = job.deal
    _company_facts(job, company, deal, research.company_facts)
    recorded = {(_norm(name), d.year if d else None)
                for name, d in company.funding_rounds.values_list("round_name", "announced_on")}
    for r in research.past_rounds:
        year = int(r.announced_on[:4]) if r.announced_on and r.announced_on[:4].isdigit() else None
        if (_norm(r.round_name), year) in recorded:
            continue
        leads = ", ".join(i.name for i in r.lead_investors)
        when = f" ({r.announced_on[:7]})" if r.announced_on else ""
        label = f"{r.round_name}{when}: {_fmt_amount(r.amount, r.currency) or 'amount not disclosed'}" \
                + (f", led by {leads}" if leads else "")
        if r.post_money or r.pre_money:
            label += f", post-money {_fmt_amount(r.post_money or (r.pre_money or 0) + (r.amount or 0), r.currency)}"
        _suggest(job, company, deal, Suggestion.Target.PAST_ROUND, r.model_dump(), label, r.source_url,
                 ", ".join(i.name for i in r.other_investors) or None, r.confidence)
    _people(job, company, deal, research.people)
    known_news = {_norm(s) for s in company.interactions.values_list("summary", flat=True)}
    for n in research.news:
        if _norm(n.headline) in known_news:
            continue
        _suggest(job, company, deal, Suggestion.Target.NEWS, n.model_dump(),
                 f"{n.date or ''} {n.headline}".strip(), n.source_url, n.summary)


def run_research(job):
    company = job.company
    known = [f"Contact: {c.full_name} ({c.role or ''})" for c in company.contacts.all()]
    known += [f"Valuation {v.valuation_date}: {v.get_method_display()}" for v in company.valuations.all()]
    research, usage, report = claude.research_company(company, known)
    _record_usage(job, usage)
    suggestions_from_research(job, company, research)
    count = job.suggestions.count()
    job.summary = f"{count} new suggestion{'s' if count != 1 else ''}." + (
        f" Note: {research.caveats}" if research.caveats else "")


def run_cap_table(job):
    from core.models import InvestingEntity

    company = job.company
    docs = list(company.documents.filter(pk__in=job.params.get("documents", [])))
    if not docs:
        raise claude.AiError("The selected documents no longer exist.")
    payload = []
    for d in docs:
        with d.file.open("rb") as fh:
            payload.append({"title": d.title, "filename": d.original_filename, "mime_type": d.mime_type,
                            "data": fh.read()})
    entities = list(InvestingEntity.objects.values_list("name", flat=True))
    reading, usage, notes = claude.read_cap_table(company.name, entities, payload)
    _record_usage(job, usage)
    snap = reading.snapshot
    if snap is None or not snap.classes:
        job.summary = "The documents do not describe a capitalization." + (f" {reading.note}" if reading.note else "")
        return
    closing = parse_date_or_none(snap.closing_date)
    shares = sum(c.shares_outstanding for c in snap.classes)
    label = (f"Cap table after {snap.round_name or 'the closing'}" + (f" ({closing})" if closing else "")
             + f": {len(snap.classes)} classes, {shares:,} shares issued"
             + (f", {snap.fully_diluted_total:,} fully diluted" if snap.fully_diluted_total else ""))
    mine = sum(h.shares for h in snap.our_holdings)
    if mine:
        label += f", ours {mine:,}"
    names = ", ".join(d.title for d in docs)
    _suggest(job, company, None, Suggestion.Target.CAP_TABLE, snap.model_dump(), label, None,
             f"{snap.evidence} Documents: {names}." + (f" Caveats: {snap.caveats}" if snap.caveats else ""))
    job.summary = f"{company.name}: cap table proposal from {len(docs)} document(s)." + (
        f" {'; '.join(notes)}" if notes else "")


# --- Email ---------------------------------------------------------------------------------

def _company_key(name):
    name = re.sub(LEGAL_SUFFIXES, "", _norm(name))
    return re.sub(r"[^a-z0-9]+", "", name)


def _domain(url_or_email):
    if not url_or_email:
        return None
    text = url_or_email.split("@")[-1] if "@" in url_or_email and "//" not in url_or_email else url_or_email
    host = urlsplit(text if "//" in text else f"//{text}").hostname or ""
    return host.removeprefix("www.") or None


def match_company(name, website):
    key, domain = _company_key(name) if name else None, _domain(website)
    for company in Company.objects.all():
        names = [company.name, *(company.aliases or "").splitlines()]
        if key and any(_company_key(n) == key for n in names if n.strip()):
            return company
        if domain and _domain(company.website) == domain:
            return company
    return None


def _folder(company, name):
    """The company's folder with this name (any capitalisation), created if needed."""
    name = " ".join((name or "").split())[:100]
    if not name:
        return None
    folder = company.document_folders.filter(name__iexact=name).first()
    return folder or DocumentFolder.objects.create(company=company, name=name)


def _save_attachments(company, deal, attachments, infos):
    by_name = {i.filename: i for i in infos}
    saved = []
    for att in attachments:
        sha = hashlib.sha256(att["data"]).hexdigest()
        info = by_name.get(att["filename"])
        existing = Document.objects.filter(sha256=sha, company=company).first()
        if existing:
            if existing.folder_id is None and info and info.folder:
                existing.folder = _folder(company, info.folder)
                existing.save(update_fields=["folder"])
            saved.append(existing)
            continue
        doc = Document(company=company, deal=deal, folder=_folder(company, info.folder) if info else None,
                       category=info.category if info else "other",
                       title=(info.title if info else att["filename"])[:300],
                       document_date=parse_date_or_none(info.document_date) if info and info.document_date else None,
                       notes="Received by email")
        doc.file = ContentFile(att["data"], name=att["filename"])  # saved, and hashed, by doc.save()
        doc.save()
        saved.append(doc)
    return saved


def _events_and_figures(job, company, deal, extraction):
    """Announced meetings and report figures, as suggestions (events already recorded are skipped)."""
    seen = set()
    for e in extraction.events:
        when = parse_date_or_none(e.date)
        key = (e.kind, when)
        if when is None or key in seen or company.interactions.filter(kind=e.kind, occurred_on=when).exists():
            continue
        seen.add(key)
        label = f"{when} {EVENT_LABELS[e.kind]}" + (f", {e.time}" if e.time else "") \
            + (f", {e.location}" if e.location else "")
        _suggest(job, company, deal, Suggestion.Target.EVENT, e.model_dump(), label, None, e.agenda or None)
    for f in extraction.report_figures:
        when = parse_date_or_none(f.as_of)
        if when is None or company.metrics.filter(as_of=when).exists():
            continue
        parts = [f"{name} {_fmt_amount(value, f.currency or 'CHF')}" for name, value in
                 (("cash", f.cash), ("burn/month", f.monthly_burn), ("revenue TTM", f.revenue_ttm)) if value]
        if f.headcount:
            parts.append(f"{f.headcount} employees")
        if parts:
            _suggest(job, company, deal, Suggestion.Target.FIGURES, f.model_dump(),
                     f"Figures as of {when}: " + ", ".join(parts))


def parse_date_or_none(value):
    try:
        return parse_date(value)
    except SuggestionError:
        return None


EVENT_LABELS = {"general_assembly": "General assembly", "board": "Board meeting", "meeting": "Meeting"}


def run_email(job):
    inbound = job.inbound_email
    InboundEmail.objects.filter(pk=inbound.pk).update(status=InboundEmail.Status.PROCESSING)
    with inbound.raw.open("rb") as fh:
        info = mail.parse_eml(fh.read())
    extraction, usage, notes = claude.extract_email(info["subject"], info["sender"], info["sent_at"],
                                                    info["body"], info["attachments"])
    _record_usage(job, usage)
    inbound.summary = extraction.summary

    company = inbound.company or match_company(extraction.company_name, extraction.company_website)
    if company is None and not extraction.company_name:
        inbound.status = InboundEmail.Status.NEEDS_COMPANY
        inbound.note = "Claude could not tell which company the email is about. Choose it and analyse again."
        inbound.save()
        job.summary = inbound.note
        return
    with transaction.atomic():
        if company is None:
            company = Company.objects.create(name=extraction.company_name.strip(),
                                             website=extraction.company_website or None)
        deal = inbound.deal or company.deals.filter(stage__in=OPEN_STAGES).order_by("-opened_on", "-id").first()
        # A portfolio company sending a report or a convocation is not a new deal, unless the email is about a round.
        about_a_round = bool(extraction.deal_facts or extraction.round_investors)
        if deal is None and (company.status != Company.Status.PORTFOLIO or about_a_round):
            source = Contact.objects.filter(email__iexact=info["sender"]).first()
            deal = Deal.objects.create(company=company, stage=DealStage.SOURCED, source_contact=source,
                                       source_note=f"Email from {info['sender']}: {info['subject']}"[:500])
        documents = _save_attachments(company, deal, info["attachments"], extraction.attachments)
        Interaction.objects.create(company=company, deal=deal, kind="email",
                                   occurred_on=(info["sent_at"] or timezone.now()).date(),
                                   summary=f"Email from {info['sender']}: {info['subject']}"[:500],
                                   details=extraction.summary)
        job.company, job.deal = company, deal

        _company_facts(job, company, deal, extraction.company_facts)
        for f in extraction.deal_facts if deal else []:
            if _norm(getattr(deal, f.field, None)) == _norm(f.value):
                continue
            _suggest(job, company, deal, Suggestion.Target.DEAL_FIELD, {"field": f.field, "value": f.value},
                     f"{FIELD_LABELS[f.field]}: {f.value}", None, f.evidence or None, f.confidence)
        for r in extraction.round_investors if deal else []:
            amount = f", {_fmt_amount(r.amount, r.currency or deal.currency)}" if r.amount else ""
            _suggest(job, company, deal, Suggestion.Target.PARTICIPANT, r.model_dump(),
                     f"{r.name} ({r.kind}) – {r.role.replace('_', '-')}, {r.status}{amount}")
        _people(job, company, deal, extraction.people, [r.name for r in extraction.round_investors])
        _events_and_figures(job, company, deal, extraction)
        if extraction.key_terms and deal:
            _suggest(job, company, deal, Suggestion.Target.DEAL_NOTE,
                     {"heading": f"Key terms from email '{info['subject']}'", "text": extraction.key_terms},
                     "Key investment terms: " + extraction.key_terms.replace("\n", "; ")[:300])

        inbound.company, inbound.deal = company, deal
        inbound.status = InboundEmail.Status.DONE
        inbound.note = "; ".join([f"{len(documents)} document(s) saved"] + notes)
        inbound.save()
    count = job.suggestions.count()
    job.summary = f"{company.name}: {count} suggestion{'s' if count != 1 else ''}, {len(documents)} document(s)."
