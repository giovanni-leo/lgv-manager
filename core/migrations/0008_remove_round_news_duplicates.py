"""Remove the news items that accepting a past round created before the funding
history existed: the rounds are now in funding_rounds (migration 0007), so the
news items only repeat them. Only items matching an accepted past-round
suggestion exactly (same company, kind 'news', same text) are removed.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    Suggestion = apps.get_model("core", "Suggestion")
    Interaction = apps.get_model("core", "Interaction")
    accepted = Suggestion.objects.filter(target="past_round", status="accepted", company__isnull=False)
    for company_id, label in accepted.values_list("company_id", "label"):
        Interaction.objects.filter(company_id=company_id, kind="news", summary=label, deal__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0007_funding_history_backfill")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
