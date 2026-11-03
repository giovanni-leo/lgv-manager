"""Shared field helpers and choices."""

from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.db.models.functions import Now
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Currency(models.TextChoices):
    CHF = "CHF", "CHF"
    EUR = "EUR", "EUR"
    USD = "USD", "USD"


def currency_field(**kwargs):
    """A 3-letter ISO currency code, defaulting to CHF (also at DB level)."""
    kwargs.setdefault("verbose_name", _("currency"))
    return models.CharField(
        max_length=3, choices=Currency.choices, default="CHF", db_default="CHF", **kwargs
    )


def currency_check(name, field="currency"):
    """DB check: currency is three upper-case letters (allows future currencies)."""
    return models.CheckConstraint(
        condition=Q(**{f"{field}__regex": r"^[A-Z]{3}$"}), name=name
    )


def money_field(verbose_name, **kwargs):
    kwargs.setdefault("null", True)
    kwargs.setdefault("blank", kwargs["null"])
    return models.DecimalField(max_digits=18, decimal_places=2, verbose_name=verbose_name, **kwargs)


def price_field(verbose_name, **kwargs):
    kwargs.setdefault("null", True)
    kwargs.setdefault("blank", True)
    return models.DecimalField(max_digits=18, decimal_places=6, verbose_name=verbose_name, **kwargs)


def created_at_field():
    return models.DateTimeField(_("created at"), default=timezone.now, db_default=Now(), editable=False)


def updated_at_field():
    return models.DateTimeField(_("updated at"), auto_now=True, db_default=Now(), editable=False)


def today():
    return timezone.localdate()


ZERO = Decimal("0")
