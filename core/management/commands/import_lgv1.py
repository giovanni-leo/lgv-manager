"""Import the LGV1 portfolio from the backoffice workbook.

    python manage.py import_lgv1 /path/to/portfolio.xlsx            # dry run: shows what would be created
    python manage.py import_lgv1 /path/to/portfolio.xlsx --apply    # creates or updates it
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from core.models import InvestingEntity
from core.services import import_portfolio as imp


class Command(BaseCommand):
    help = "Import the LGV1 portfolio (positions without shares, at the fair value reported by the fund)."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--sheet", default="Portfolio Summary v3")
        parser.add_argument("--fund", default="LGV1", help="Code of the fund the positions belong to.")
        parser.add_argument("--apply", action="store_true", help="Write to the database (default: dry run).")

    def handle(self, *args, path, sheet, fund, apply, **options):
        target = InvestingEntity.objects.filter(code=fund).first()
        if target is None:
            raise CommandError(f"Fund {fund} does not exist: run `manage.py setup_funds` first.")
        try:
            book = load_workbook(path, data_only=True)
            report = imp.read_positions(book, sheet)
        except (OSError, KeyError, ValueError) as exc:
            raise CommandError(f"Cannot read {path} (sheet {sheet!r}): {exc}") from exc
        imp.check_totals(book, sheet, report)
        self.stdout.write(f"Sheet {sheet!r}, values as of {report.as_of}, fund {target.code} ({target.base_currency})")
        self.stdout.write(f"{'row':>4} {'company':<34} {'acct':<4} {'status':<12} {'date':<11} {'cost':>12} {'value':>12} {'realized':>10}")
        for p in report.positions:
            self.stdout.write(f"{p.row:>4} {p.company[:34]:<34} {p.account:<4} {p.status:<12} {str(p.invested_on or ''):<11} "
                              f"{p.cost if p.cost is not None else '–':>12} {p.fair_value if p.fair_value is not None else '–':>12} "
                              f"{p.realized if p.realized else '':>10}")
        for w in report.warnings:
            self.stdout.write(self.style.WARNING("! " + w))
        if not apply:
            self.stdout.write(self.style.NOTICE(f"Dry run: {len(report.positions)} position(s) read, nothing written. "
                                                "Add --apply to import."))
            return
        with transaction.atomic():
            imp.apply_import(report, target, path.rsplit("/", 1)[-1])
        self.stdout.write(self.style.SUCCESS(
            f"Imported: {report.created} investment(s) created, {report.updated} updated."))
