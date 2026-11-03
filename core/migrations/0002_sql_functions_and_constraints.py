"""Ownership SQL functions/views and database-level referential actions."""

from pathlib import Path

from django.db import migrations

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def apply_constraints(apps, schema_editor):
    from core import db_constraints

    with schema_editor.connection.cursor() as cursor:
        db_constraints.apply_sql(cursor)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.RunPython(apply_constraints, migrations.RunPython.noop),
        migrations.RunSQL(
            (SQL_DIR / "ownership.sql").read_text(),
            reverse_sql=(SQL_DIR / "ownership_drop.sql").read_text(),
        ),
    ]
