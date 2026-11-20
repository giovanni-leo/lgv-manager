"""Database-level ON DELETE for the document folder foreign keys (see 0005 for why this is a separate step)."""

from django.db import migrations


def apply_constraints(apps, schema_editor):
    from core import db_constraints

    with schema_editor.connection.cursor() as cursor:
        db_constraints.apply_sql(cursor)


class Migration(migrations.Migration):
    dependencies = [("core", "0009_document_folders_and_categories")]

    operations = [migrations.RunPython(apply_constraints, migrations.RunPython.noop)]
