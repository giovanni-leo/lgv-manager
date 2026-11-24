"""Move everything of one fund to another and delete the first (two entries for the same fund).

    python manage.py merge_funds EFH EFH-SPV            # dry run: shows what would move
    python manage.py merge_funds EFH EFH-SPV --apply    # moves investments, realizations and deals, deletes EFH
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from core.models import Deal, InvestingEntity, Investment, Realization


def find(ref):
    fund = InvestingEntity.objects.filter(Q(code=ref) | Q(name=ref)).first()
    if fund is None:
        raise CommandError(f"No fund with the short name or name {ref!r}.")
    return fund


class Command(BaseCommand):
    help = "Move investments, realizations and deals of the first fund to the second, then delete the first."

    def add_arguments(self, parser):
        parser.add_argument("remove", help="Short name (or name) of the fund to remove, e.g. EFH.")
        parser.add_argument("keep", help="Short name (or name) of the fund to keep, e.g. EFH-SPV.")
        parser.add_argument("--apply", action="store_true", help="Do it (default: dry run).")

    def handle(self, *args, remove, keep, apply, **options):
        source, target = find(remove), find(keep)
        if source.pk == target.pk:
            raise CommandError("Choose two different funds.")
        investments = Investment.objects.filter(entity=source)
        realizations = Realization.objects.filter(entity=source)
        deals = Deal.objects.filter(fund=source)
        self.stdout.write(f"From {source.label} ({source.name}, {source.base_currency}) "
                          f"to {target.label} ({target.name}, {target.base_currency}):")
        self.stdout.write(f"  {investments.count()} investment(s), {realizations.count()} realization(s), "
                          f"{deals.count()} deal(s)")
        if source.base_currency != target.base_currency:
            self.stdout.write(self.style.WARNING(
                f"! The currencies differ ({source.base_currency} and {target.base_currency}): the amounts keep "
                f"their own currency, but the totals will be shown in {target.base_currency}. "
                "Change the currency of the fund to keep in Data › Funds if needed."))
        if not apply:
            self.stdout.write(self.style.NOTICE("Dry run: nothing changed. Add --apply to do it."))
            return
        with transaction.atomic():
            n_inv = investments.update(entity=target)
            n_real = realizations.update(entity=target)
            n_deal = deals.update(fund=target)
            source.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Done: {n_inv} investment(s), {n_real} realization(s) and {n_deal} deal(s) moved to {target.label}; "
            f"{source.label} deleted."))
