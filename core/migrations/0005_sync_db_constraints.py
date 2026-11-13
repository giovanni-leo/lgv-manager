"""Apply database-level ON DELETE actions to the tables created in 0004.

Kept in its own migration: Django adds the foreign keys of new tables at the
end of the migration that creates them, so syncing in that same migration
would leave duplicate constraints.
"""

from django.db import migrations


def apply_constraints(apps, schema_editor):
    from core import db_constraints

    with schema_editor.connection.cursor() as cursor:
        db_constraints.apply_sql(cursor)


class Migration(migrations.Migration):
    dependencies = [("core", "0004_claude_jobs_and_suggestions")]

    operations = [migrations.RunPython(apply_constraints, migrations.RunPython.noop)]
