"""Merge a duplicate company into the company that is kept."""

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from .models import Company, Deal
from .services.merge import MergeError, check, counts, deal_counts, merge_companies, merge_deals


@permission_required("core.change_company", raise_exception=True)
def company_merge(request, pk):
    """Page of the company to KEEP: choose the duplicate, see what moves, confirm."""
    target = get_object_or_404(Company, pk=pk)
    if not request.user.has_perm("core.delete_company"):
        raise PermissionDenied
    source_id = request.POST.get("source") or request.GET.get("source")
    source = Company.objects.filter(pk=source_id).exclude(pk=target.pk).first() if (source_id or "").isdigit() else None
    problem = None
    if source:
        try:
            check(target, source)
        except MergeError as exc:
            problem = str(exc)
    if request.method == "POST" and source and not problem and request.POST.get("confirm"):
        name = source.name
        report = merge_companies(target, source)
        moved = ", ".join(f"{n} {label.lower()}" for label, n in report.moved.items()) or "nothing to move"
        messages.success(request, f"“{name}” was merged into “{target.name}”: {moved}. "
                                  f"The name “{name}” is now recognised as {target.name}.")
        for line in report.dropped:
            messages.info(request, line)
        return redirect("company_detail", pk=target.pk)
    return render(request, "app/company/merge.html", {
        "target": target, "source": source, "problem": problem,
        "others": Company.objects.exclude(pk=target.pk).order_by("name"),
        "target_counts": counts(target), "source_counts": counts(source) if source else [],
        "nav_section": "portfolio" if target.status != "prospect" else "pipeline"})


@permission_required("core.change_deal", raise_exception=True)
def deal_merge(request, pk):
    """Page of the deal to KEEP: choose another deal of the same company, see what moves, confirm."""
    keep = get_object_or_404(Deal.objects.select_related("company"), pk=pk)
    if not request.user.has_perm("core.delete_deal"):
        raise PermissionDenied
    others = Deal.objects.filter(company=keep.company).exclude(pk=keep.pk).order_by("-opened_on", "-id")
    source_id = request.POST.get("source") or request.GET.get("source")
    source = others.filter(pk=source_id).first() if (source_id or "").isdigit() else None
    if request.method == "POST" and source and request.POST.get("confirm"):
        report = merge_deals(keep, source)
        moved = ", ".join(f"{n} {label.lower()}" for label, n in report.moved.items()) or "nothing to move"
        messages.success(request, f"The duplicate deal was merged: {moved}.")
        for line in report.dropped:
            messages.info(request, line)
        return redirect("deal_detail", pk=keep.pk)
    return render(request, "app/deal/merge.html", {
        "deal": keep, "company": keep.company, "source": source, "others": others, "nav_section": "pipeline",
        "keep_counts": deal_counts(keep), "source_counts": deal_counts(source) if source else []})
