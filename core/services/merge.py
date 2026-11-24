"""Merge a duplicate company into the company that is kept.

Everything that belongs to the duplicate moves to the kept company: deals, contacts, documents and folders,
events, figures, milestones, funding rounds, valuations, investments, share classes and events, Claude's
suggestions, jobs and emails. Fields the kept company has empty are filled from the duplicate, and the
duplicate's name is added to the kept company's other names, so the same name is recognised next time.

Cap tables are not combined: if both companies have share classes or share events, adding them would count
shares twice, so the merge is refused and the cap tables must be sorted out first.
"""

from dataclasses import dataclass, field

from django.db import transaction

from core.models import (AiJob, Company, CompanyMetric, Contact, Deal, Document, DocumentFolder, DocumentLink,
                         FundingRound, FundingRoundInvestor, InboundEmail, Interaction, Investment, Milestone,
                         OptionPool, Realization, ShareClass, ShareEvent, Suggestion, Valuation)

COPY_IF_EMPTY = ["legal_name", "country", "city", "website", "sector", "description", "data_room_url",
                 "reporting_frequency"]

COUNTED = [("Deals", "deals"), ("Contacts", "contacts"), ("Documents", "documents"),
           ("Events and notes", "interactions"), ("Funding rounds", "funding_rounds"),
           ("Share classes", "share_classes"), ("Share events", "share_events"), ("Investments", "investments"),
           ("Valuations", "valuations"), ("Company figures", "metrics"), ("Milestones", "milestones"),
           ("Claude suggestions", "suggestions")]


class MergeError(Exception):
    pass


@dataclass
class MergeReport:
    moved: dict = field(default_factory=dict)
    dropped: list = field(default_factory=list)


def counts(company):
    """What a company holds: [(label, number)], only the non-zero ones."""
    rows = [(label, getattr(company, attr).count()) for label, attr in COUNTED]
    return [(label, n) for label, n in rows if n]


def check(target, source):
    if target.pk == source.pk:
        raise MergeError("Choose two different companies.")
    has_cap = lambda c: c.share_classes.exists() or c.share_events.exists()  # noqa: E731
    if has_cap(target) and has_cap(source):
        raise MergeError("Both companies have a cap table (share classes or events). Merging them would count "
                         "shares twice: delete the cap table of the duplicate first, or keep the one that is "
                         "complete and re-enter the other by hand.")


def _move(queryset, target, report, label):
    n = queryset.update(company=target)
    if n:
        report.moved[label] = report.moved.get(label, 0) + n


@transaction.atomic
def merge_companies(target, source):
    """Move everything of ``source`` to ``target`` and delete ``source``."""
    check(target, source)
    report = MergeReport()

    # Documents: the same file (same content) is kept once; its references point to the kept copy.
    kept = {d.sha256: d for d in target.documents.all()}
    for doc in source.documents.all():
        twin = kept.get(doc.sha256)
        if twin is None:
            continue
        DocumentLink.objects.filter(document=doc).update(document=twin)
        ShareEvent.objects.filter(source_document=doc).update(source_document=twin)
        Investment.objects.filter(source_document=doc).update(source_document=twin)
        report.dropped.append(f"Document “{doc.title}” was already there")
        doc.delete()
    for folder in source.document_folders.all():
        same = target.document_folders.filter(name__iexact=folder.name).first()
        if same:
            Document.objects.filter(folder=folder).update(folder=same)
            folder.delete()
    _move(source.document_folders.all(), target, report, "Document folders")
    _move(source.documents.all(), target, report, "Documents")

    # Rows with one entry per date: keep the kept company's, move the others.
    for model, field_name, label in ((CompanyMetric, "as_of", "Company figures"),
                                     (OptionPool, "as_of", "Option pools")):
        have = set(model.objects.filter(company=target).values_list(field_name, flat=True))
        clash = model.objects.filter(company=source, **{f"{field_name}__in": have})
        if clash.exists():
            report.dropped.append(f"{clash.count()} {label.lower()} on a date the kept company already has")
            clash.delete()
        _move(model.objects.filter(company=source), target, report, label)

    # Funding rounds: the same round (name and year) found twice becomes one, with the investors of both.
    for rnd in list(source.funding_rounds.all()):
        same = None
        if rnd.announced_on:
            same = target.funding_rounds.filter(round_name__iexact=rnd.round_name,
                                                announced_on__year=rnd.announced_on.year).first()
        if same:
            for fri in rnd.investors.all():
                FundingRoundInvestor.objects.get_or_create(funding_round=same, co_investor=fri.co_investor,
                                                           defaults={"is_lead": fri.is_lead})
            for attr in ("amount", "pre_money", "post_money", "source_url", "notes"):
                if getattr(same, attr) in (None, "") and getattr(rnd, attr) not in (None, ""):
                    setattr(same, attr, getattr(rnd, attr))
            same.save()
            report.dropped.append(f"Round “{rnd.round_name}” was already there: investors combined")
            rnd.delete()

    # Cap table data and everything else simply changes owner (the database checks all links at the end).
    for model, label in ((ShareClass, "Share classes"), (ShareEvent, "Share events"), (Investment, "Investments"),
                         (Realization, "Realizations"), (Valuation, "Valuations"), (Deal, "Deals"),
                         (Contact, "Contacts"), (Interaction, "Events and notes"), (Milestone, "Milestones"),
                         (FundingRound, "Funding rounds"), (Suggestion, "Claude suggestions"),
                         (AiJob, "Claude jobs"), (InboundEmail, "Emails")):
        _move(model.objects.filter(company=source), target, report, label)

    for name in COPY_IF_EMPTY:
        if not getattr(target, name) and getattr(source, name):
            setattr(target, name, getattr(source, name))
    if target.status == Company.Status.PROSPECT and source.status != Company.Status.PROSPECT:
        target.status = source.status
    if target.board_role == "none" and source.board_role != "none":
        target.board_role = source.board_role

    known = {n.strip().lower() for n in [target.name, *(target.aliases or "").splitlines()] if n.strip()}
    names = list((target.aliases or "").splitlines())
    for n in (source.name, source.legal_name, *(source.aliases or "").splitlines()):
        if n and n.strip().lower() not in known:
            known.add(n.strip().lower())
            names.append(n.strip())
    target.aliases = "\n".join(n for n in names if n.strip()) or None
    target.save()

    source.refresh_from_db()
    source.delete()
    return report

