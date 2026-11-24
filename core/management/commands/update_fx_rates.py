"""Load exchange rates from the European Central Bank (see core/services/ecb.py)."""

from django.core.management.base import BaseCommand, CommandError

from core.services import ecb


class Command(BaseCommand):
    help = "Add the missing daily exchange rates (CHF per unit) from the ECB. Use --full the first time."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Whole history since 1999 instead of 90 days.")

    def handle(self, *args, full=False, **options):
        try:
            added = ecb.update(full=full)
        except Exception as exc:  # noqa: BLE001 - network, format
            raise CommandError(f"Could not load the rates: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"{added} exchange rate(s) added."))
