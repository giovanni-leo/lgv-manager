"""The Documents page: search inside every file, filter by company, category, folder and year."""

from django.contrib.auth.decorators import permission_required
from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .funds import active_fund, visible_company_ids
from .models import Company, Document, DocumentFolder

PAGE_SIZE = 40
OPEN, CLOSE = "\x02", "\x03"


def _snippet(text):
    """Headline from the database, with the matching words in <mark>; everything else escaped."""
    return mark_safe(escape(text or "").replace(OPEN, "<mark>").replace(CLOSE, "</mark>"))


@permission_required("core.view_document", raise_exception=True)
def documents(request):
    g = request.GET
    q = (g.get("q") or "").strip()[:200]
    base = Document.objects.all()
    fund = active_fund(request)
    visible = None
    if fund:                          # documents of the selected fund's companies (and of companies without a fund yet)
        visible = visible_company_ids(fund)
        base = base.filter(Q(company_id__in=visible) | Q(company__isnull=True, deal__company_id__in=visible))
    qs = base.select_related("company", "folder", "deal")
    if g.get("company", "").isdigit():
        qs = qs.filter(company_id=int(g["company"]))
    if g.get("category") in {c.value for c in Document.Category}:
        qs = qs.filter(category=g["category"])
    if g.get("folder", "").isdigit():
        qs = qs.filter(folder_id=int(g["folder"]))
    if g.get("year", "").isdigit():
        qs = qs.filter(document_date__year=int(g["year"]))
    if q:
        query = SearchQuery(q, config="simple", search_type="websearch")
        qs = qs.filter(Q(search=query) | Q(title__icontains=q) | Q(original_filename__icontains=q))
        qs = qs.annotate(
            rank=SearchRank("search", query),
            headline=SearchHeadline("extracted_text", query, config="simple", start_sel=OPEN, stop_sel=CLOSE,
                                    max_words=28, min_words=12, max_fragments=2, fragment_delimiter=" … "),
        ).order_by("-rank", "-document_date", "-uploaded_at")
    else:
        qs = qs.order_by("-document_date", "-uploaded_at")

    page = Paginator(qs, PAGE_SIZE).get_page(g.get("page"))
    for doc in page:
        doc.snippet = _snippet(getattr(doc, "headline", "")) if q and doc.extracted_text else ""
    years = sorted({d.year for d in base.exclude(document_date=None).dates("document_date", "year")}, reverse=True)
    folders = DocumentFolder.objects.select_related("company")
    companies = Company.objects.filter(documents__isnull=False).distinct().order_by("name")
    if visible is not None:
        folders, companies = folders.filter(company_id__in=visible), companies.filter(pk__in=visible)
    if g.get("company", "").isdigit():
        folders = folders.filter(company_id=int(g["company"]))
    keep = g.copy()
    keep.pop("page", None)
    return render(request, "app/documents.html", {
        "page": page, "q": q, "total": page.paginator.count,
        "companies": companies,
        "categories": Document.Category.choices, "years": years, "folders": folders,
        "selected": {k: g.get(k, "") for k in ("company", "category", "folder", "year")},
        "querystring": keep.urlencode(),
    })
