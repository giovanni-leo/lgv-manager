"""Sync DB-level foreign keys for the funding tables and fill the funding history
from past-round suggestions that were already accepted (before the history existed,
accepting one only created a news item and a valuation).
"""

from django.db import migrations


def forwards(apps, schema_editor):
    from core import db_constraints

    with schema_editor.connection.cursor() as cursor:
        db_constraints.apply_sql(cursor)
    backfill(apps)


def backfill(apps):
    from core.ai.funding import record_funding_round

    Suggestion = apps.get_model("core", "Suggestion")
    models = (apps.get_model("core", "FundingRound"), apps.get_model("core", "FundingRoundInvestor"),
              apps.get_model("core", "CoInvestor"))
    accepted = Suggestion.objects.filter(target="past_round", status="accepted", company__isnull=False)
    for suggestion in accepted.select_related("company").order_by("id"):
        record_funding_round(suggestion.company, suggestion.payload, *models)


class Migration(migrations.Migration):
    dependencies = [("core", "0006_funding_rounds")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
