"""Set up the funds (EFH, LGV1, LGV2) and give them their currencies. Safe to run again.

    python manage.py setup_funds                       # create or update the three funds
    python manage.py setup_funds --assign-existing EFH # also: every existing investment, realization and deal
                                                       # without a fund goes to EFH (run once)
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Deal, InvestingEntity, Investment, Realization

FUNDS = [
    # (code, name, currency, names it may already have in the database)
    ("EFH", "Efficient Frontier Holding SA", "USD", ["Efficient Frontier Holding SA", "Efficient Frontier Holding",
                                                     "EFH-SPV", "EFH SPV"]),
    ("LGV1", "Lake Geneva Ventures 1", "CHF", ["Lake Geneva Ventures 1"]),
    ("LGV2", "Lake Growth Ventures 2", "EUR", ["Lake Growth Ventures 2", "Lake Growth Ventures"]),
]


class Command(BaseCommand):
    help = "Create or update the funds EFH (USD), LGV1 (CHF) and LGV2 (EUR)."

    def add_arguments(self, parser):
        parser.add_argument("--assign-existing", metavar="CODE",
                            help="Give this fund every existing investment, realization and deal that has none.")

    @transaction.atomic
    def handle(self, *args, assign_existing=None, **options):
        by_code = {}
        for code, name, currency, aliases in FUNDS:
            fund = InvestingEntity.objects.filter(code=code).first() \
                or InvestingEntity.objects.filter(code__in=aliases).first() \
                or InvestingEntity.objects.filter(name__in=aliases).first()
            created = fund is None
            fund = fund or InvestingEntity(name=name, code=code)
            fund.kind, fund.base_currency = InvestingEntity.Kind.FUND, currency        # an existing short name is kept
            if not fund.code:
                fund.code = code
            fund.save()
            by_code[code] = fund
            by_code.setdefault(fund.code, fund)
            self.stdout.write(f"{'Created' if created else 'Updated'}: {code} – {fund.name} ({currency})")
        if assign_existing:
            if assign_existing not in by_code:
                raise CommandError(f"Unknown fund {assign_existing!r}: use one of {', '.join(by_code)}.")
            target = by_code[assign_existing]
            # Investments and realizations always have an entity: move those of entities that are not funds.
            others = InvestingEntity.objects.exclude(pk__in=[f.pk for f in by_code.values()])
            n_inv = Investment.objects.filter(entity__in=others).update(entity=target)
            n_real = Realization.objects.filter(entity__in=others).update(entity=target)
            n_deal = Deal.objects.filter(fund__isnull=True).exclude(stage__in=["sourced", "passed", "on_hold"]).update(
                fund=target)
            self.stdout.write(self.style.SUCCESS(
                f"{n_inv} investment(s), {n_real} realization(s) and {n_deal} deal(s) assigned to {assign_existing}."))
        self.stdout.write(self.style.SUCCESS("Funds ready."))
