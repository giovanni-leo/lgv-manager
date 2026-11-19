"""Formatting for at-a-glance pages."""

from datetime import date
from decimal import Decimal

from django import template
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

register = template.Library()


def _swiss(number, decimals=0):
    return f"{number:,.{decimals}f}".replace(",", "'")


@register.filter
def money(value, currency=""):
    """Compact amount: CHF 3.5M, CHF 750k, CHF 1'250."""
    if value in (None, ""):
        return "–"
    value = Decimal(value)
    sign, magnitude = ("-" if value < 0 else ""), abs(value)
    if magnitude >= 1_000_000_000:
        text = f"{magnitude / 1_000_000_000:.2f}".rstrip("0").rstrip(".") + "bn"
    elif magnitude >= 1_000_000:
        text = f"{magnitude / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    elif magnitude >= 10_000:
        text = f"{magnitude / 1_000:.0f}k"
    else:
        text = _swiss(magnitude)
    return f"{currency} {sign}{text}".strip()


@register.filter
def num(value, decimals=0):
    if value in (None, ""):
        return "–"
    return _swiss(Decimal(value), int(decimals))


@register.filter
def pct(value, decimals=2):
    if value in (None, ""):
        return "–"
    return f"{Decimal(value):.{int(decimals)}f}%"


@register.filter
def ratio_pct(value, decimals=1):
    """A fraction (0.25) as a percentage (25.0%)."""
    if value in (None, ""):
        return "–"
    text = f"{Decimal(value) * 100:.{int(decimals)}f}%"
    return text[1:] if text.startswith("-") and float(text[1:-1]) == 0 else text  # no "-0.0%"


@register.filter
def multiple(value):
    return "–" if value in (None, "") else f"{Decimal(value):.2f}×"


@register.filter
def days_left(value):
    """'in 5 days', 'today', '3 days overdue'."""
    if not isinstance(value, date):
        return ""
    days = (value - timezone.localdate()).days
    if days == 0:
        return _("today")
    if days > 0:
        return ngettext("in %(n)d day", "in %(n)d days", days) % {"n": days}
    return ngettext("%(n)d day overdue", "%(n)d days overdue", -days) % {"n": -days}


@register.filter
def due_class(value, warn_days=7):
    """CSS class for a deadline: overdue, soon or nothing."""
    if not isinstance(value, date):
        return ""
    days = (value - timezone.localdate()).days
    if days < 0:
        return "is-overdue"
    if days <= int(warn_days):
        return "is-soon"
    return ""


@register.filter
def can(user, perm):
    """{% if user|can:'change_deal' %}"""
    return user.has_perm(f"core.{perm}")


def _page_path(request):
    """The page the user is on, also when rendering an HTMX fragment."""
    from urllib.parse import urlsplit

    current = request.headers.get("HX-Current-URL")
    if current:
        parts = urlsplit(current)
        return parts.path + (f"?{parts.query}" if parts.query else "")
    return request.get_full_path()


@register.simple_tag(takes_context=True)
def admin_add(context, model, **initial):
    """Link to the full form for a new record, prefilled, coming back here after saving."""
    from urllib.parse import urlencode

    from django.urls import reverse

    params = {k: v for k, v in initial.items() if v not in (None, "")}
    params["next"] = _page_path(context["request"])
    return f"{reverse(f'admin:core_{model}_add')}?{urlencode(params)}"


@register.simple_tag(takes_context=True)
def admin_change(context, model, pk):
    """Link to the full form of a record (every field), coming back here after saving."""
    from urllib.parse import urlencode

    from django.urls import reverse

    return f"{reverse(f'admin:core_{model}_change', args=[pk])}?{urlencode({'next': _page_path(context['request'])})}"
