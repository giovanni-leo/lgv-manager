"""Form widgets."""

from django import forms


class IsoDateInput(forms.DateInput):
    """A date typed as YYYY-MM-DD, whatever the browser's language.

    The browser's own date picker (type="date") shows MM/DD/YYYY or DD.MM.YYYY depending on the
    computer's settings, which led to mixed formats in the data.
    """

    def __init__(self, attrs=None):
        defaults = {"placeholder": "YYYY-MM-DD", "pattern": r"\d{4}-\d{2}-\d{2}", "inputmode": "numeric",
                    "maxlength": "10", "size": "11", "autocomplete": "off", "title": "YYYY-MM-DD"}
        super().__init__({**defaults, **(attrs or {})}, format="%Y-%m-%d")
