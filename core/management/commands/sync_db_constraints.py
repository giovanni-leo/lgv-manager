from django.core.management.base import BaseCommand
from django.db import connection, transaction

from core import db_constraints


class Command(BaseCommand):
    help = "Re-apply database-level ON DELETE actions and composite foreign keys."

    def handle(self, *args, **options):
        with transaction.atomic(), connection.cursor() as cursor:
            db_constraints.apply_sql(cursor)
        self.stdout.write(self.style.SUCCESS("Database constraints synchronised."))
