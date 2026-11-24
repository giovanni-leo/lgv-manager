"""Editable sections of the at-a-glance pages.

Each section is a card (or a table row) that shows a few fields and turns into
a small form in place when the user clicks "Edit" (HTMX). The registry below
says which fields each section edits and which template shows it.
"""

from dataclasses import dataclass

from django import forms
from django.db import models

from .models import Company, Deal, RoundParticipant
from .widgets import IsoDateInput

LONG_TEXT = {"notes", "description", "source_note", "pass_reason", "details"}


def formfield(db_field, **kwargs):
    field = db_field.formfield(**kwargs)
    if field is None:
        return None
    if isinstance(db_field, models.DateField):
        field.widget = IsoDateInput()
    elif isinstance(db_field, models.DecimalField):
        field.widget = forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"})
    elif isinstance(db_field, models.TextField) and not db_field.choices:
        field.widget = (forms.Textarea(attrs={"rows": 3}) if db_field.name in LONG_TEXT
                        else forms.TextInput())
    return field


@dataclass(frozen=True)
class Section:
    model: type
    fields: tuple
    template: str
    perm: str
    row: bool = False  # rendered as a table row instead of a card
    wide: bool = False  # card spans the whole page width

    def form_class(self):
        return forms.modelform_factory(self.model, fields=self.fields, formfield_callback=formfield)


SECTIONS = {
    ("deal", "round"): Section(Deal, ("fund", "round_name", "round_size", "pre_money", "proposed_ticket",
                                      "currency", "deadline"), "app/deal/_round.html", "change_deal"),
    ("deal", "next"): Section(Deal, ("next_step", "next_step_due"), "app/deal/_next.html", "change_deal"),
    ("deal", "details"): Section(Deal, ("source_contact", "source_note", "opened_on", "closed_on", "pass_reason",
                                        "notes"), "app/deal/_details.html", "change_deal"),
    ("participant", "row"): Section(RoundParticipant, ("role", "status", "amount", "currency", "contact", "notes"),
                                    "app/deal/_participant_row.html", "change_roundparticipant", row=True),
    ("company", "profile"): Section(Company, ("name", "status", "sector", "city", "country", "website",
                                              "legal_name", "description"), "app/company/_overview.html",
                                    "change_company", wide=True),
    ("company", "governance"): Section(Company, ("board_role", "reporting_frequency", "data_room_url"),
                                       "app/company/_governance.html", "change_company"),
}
