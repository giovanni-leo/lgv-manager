"""Suggestions from Claude (web research, emails) and the email inbox."""

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .ai import jobs, mail
from .ai.apply import SuggestionError, decide
from .models import AiJob, Company, Deal, InboundEmail, Suggestion


# Most decision-relevant first.
TARGET_ORDER = ["deal_field", "participant", "deal_note", "past_round", "company_field", "contact", "news"]


def suggestions_context(company, deal=None, error=None):
    pending = company.suggestions.filter(status=Suggestion.Status.PENDING).select_related("job")
    return {
        "company": company, "deal": deal, "ai_enabled": settings.AI_ENABLED,
        "pending": sorted(pending, key=lambda x: (TARGET_ORDER.index(x.target), x.created_at)),
        "latest_job": company.ai_jobs.order_by("-created_at").first(),
        "last_research": company.ai_jobs.filter(kind=AiJob.Kind.WEB_RESEARCH).exclude(status="failed")
                                .order_by("-created_at").first(),
        "suggestion_error": error,
    }


def _card(request, company, deal=None, error=None):
    return render(request, "app/_suggestions.html", suggestions_context(company, deal, error))


def _deal_from(request, company):
    pk = request.GET.get("deal") or request.POST.get("deal")
    return get_object_or_404(Deal, pk=pk, company=company) if pk else None


@permission_required("core.view_company", raise_exception=True)
def suggestions_card(request, company_pk):
    company = get_object_or_404(Company, pk=company_pk)
    return _card(request, company, _deal_from(request, company))


@require_POST
@permission_required("core.change_company", raise_exception=True)
def research_start(request, company_pk):
    company = get_object_or_404(Company, pk=company_pk)
    if not settings.AI_ENABLED:
        return HttpResponse(status=400)
    jobs.queue_research(company, _deal_from(request, company), request.user)
    return _card(request, company, _deal_from(request, company))


def _require_change(user, suggestion):
    needed = "core.change_deal" if suggestion.target in ("deal_field", "deal_note", "participant") \
        else "core.change_company"
    if not user.has_perm(needed):
        raise PermissionDenied


@require_POST
def suggestion_decide(request, pk, action):
    suggestion = get_object_or_404(Suggestion.objects.select_related("company"), pk=pk)
    _require_change(request.user, suggestion)
    try:
        decide(suggestion, request.user, accept=(action == "accept"))
    except SuggestionError as exc:
        return _card(request, suggestion.company, _deal_from(request, suggestion.company),
                     error=f"“{suggestion.label}”: {exc}")
    if action == "accept":
        response = HttpResponse(status=204)
        response["HX-Refresh"] = "true"  # show the updated page; the next suggestion stays on top
        return response
    return _card(request, suggestion.company, _deal_from(request, suggestion.company))


@require_POST
@permission_required("core.change_company", raise_exception=True)
def suggestions_decide_all(request, company_pk, action):
    company = get_object_or_404(Company, pk=company_pk)
    errors = []
    for suggestion in company.suggestions.filter(status=Suggestion.Status.PENDING):
        try:
            _require_change(request.user, suggestion)
            decide(suggestion, request.user, accept=(action == "accept"))
        except SuggestionError as exc:
            errors.append(f"“{suggestion.label}”: {exc}")
    if action == "accept" and not errors:
        response = HttpResponse(status=204)
        response["HX-Refresh"] = "true"
        return response
    return _card(request, company, _deal_from(request, company), error=" ".join(errors) or None)


# --- Inbox --------------------------------------------------------------------------------

class EmailUploadForm(forms.Form):
    eml = forms.FileField(label="Email file (.eml)")


class AssignCompanyForm(forms.Form):
    company = forms.ModelChoiceField(queryset=Company.objects.all())


@permission_required("core.view_inboundemail", raise_exception=True)
def inbox(request):
    form = EmailUploadForm()
    if request.method == "POST":
        if not request.user.has_perm("core.add_inboundemail"):
            raise PermissionDenied
        form = EmailUploadForm(request.POST, request.FILES)
        if form.is_valid():
            upload = form.cleaned_data["eml"]
            if upload.size > (settings.MAIL_MAX_ATTACHMENT_MB + 10) * 1024 * 1024:
                form.add_error("eml", "The file is too large.")
            else:
                inbound = mail.ingest_raw(upload.read(), uploaded_by=request.user)
                messages.success(request, f"Email “{inbound.subject or inbound.sender}” received: "
                                          "Claude is reading it, the result appears below in a minute or two.")
                return redirect("inbox")
    emails = InboundEmail.objects.select_related("company", "deal").prefetch_related("jobs")[:100]
    active = AiJob.objects.filter(kind=AiJob.Kind.EMAIL, status__in=["queued", "running"]).exists()
    return render(request, "app/inbox.html", {
        "form": form, "emails": emails, "active": active, "ai_enabled": settings.AI_ENABLED,
        "mailbox": settings.MAIL_IMAP_USER if mail.mailbox_configured() else None,
        "allowed": settings.MAIL_ALLOWED_SENDERS, "assign_form": AssignCompanyForm(),
    })


@require_POST
@permission_required("core.change_inboundemail", raise_exception=True)
def inbox_reprocess(request, pk):
    """Analyse an email again, optionally after choosing its company."""
    inbound = get_object_or_404(InboundEmail, pk=pk)
    form = AssignCompanyForm(request.POST)
    if form.is_valid():
        inbound.company = form.cleaned_data["company"]
    inbound.status = InboundEmail.Status.RECEIVED
    inbound.save(update_fields=["company", "status"])
    AiJob.objects.create(kind=AiJob.Kind.EMAIL, inbound_email=inbound, requested_by=request.user)
    return redirect("inbox")
