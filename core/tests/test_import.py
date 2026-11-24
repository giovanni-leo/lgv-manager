"""Import of a fund's portfolio from the backoffice workbook."""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook

from core.models import Company, InvestingEntity, Investment, Realization
from core.services import import_portfolio as imp
from core.services.portfolio import company_summary

D = Decimal


def workbook():
    book = Workbook()
    ws = book.active
    ws.title = "Portfolio Summary v3"
    ws.append([None, None, "EFI Portfolio Summary"])
    ws.append([None, None, "Updates on :", date(2025, 3, 17)])
    ws.append([])
    ws.append([None, None, "Portfolio Company", "Fund", "Status", "Company Valuation", "% Participation", "Number  of Shares",
               "FMV in portfolio", None, "Total Cost", None, "Cost as percentage", "Realized Value"])
    ws.append([None, None, "GSI"])
    ws.append([None, None, "Abionic SA", "GSI", "Open", 11230070, 0.0039, 4375, 43797.273, 40643.87, 437500, 406000, 0.02, 0])
    ws.append([None, None, "Creoptix AG", "GSI", "Exit", 1, 1, 1, 85831.0, None, 308065, None, 0.01, 335868.73])
    ws.append([None, None, "Augurix diagnostics", "GSI", "Liquidated", "n.a", "n.a", "n.a", "n.a", None, "n.a", None, None, 0])
    ws.append([None, None, "Total", None, None, None, None, None, 129628.273, None, 745565, None, None, 335868.73])
    ws.append([None, None, "LGV"])
    ws.append([None, None, "Terapet SA", "LGV", "Open", 12685250, 0.131, 2722462, 1661767.75, None, 1433636, None, 0.07, None])
    ws.append([None, None, "Total", None, None, None, None, None, 1661767.75, None, 1433636, None, None, None])
    ws.append([None, None, "GSI + LGV"])
    ws.append([None, None, "Terapet SA", "LGV", "Open", 12685250, 0.1465, 3044034, 1858135.42, None, 1574631.68])
    abionic = book.create_sheet("Abionic")
    abionic.append([None, None, "Portfolio company:", "Abionic SA"])
    abionic.append([None, None, "Investment Parameters", "GSI", "LGV"])
    abionic.append([None, None, "Investment Date", "Nov. 21", "n.a"])
    terapet = book.create_sheet("Terapet")
    terapet.append([None, None, "Investment Date", "Dec. 21", "Jun. 22"])
    return book


class ImportTests(TestCase):
    def setUp(self):
        self.lgv1 = InvestingEntity.objects.create(name="Lake Geneva Ventures 1", code="LGV1", kind="fund",
                                                   base_currency="CHF")

    def test_reading(self):
        report = imp.read_positions(workbook(), "Portfolio Summary v3")
        self.assertEqual(report.as_of, date(2025, 3, 17))
        self.assertEqual([(p.company, p.account, p.status) for p in report.positions],
                         [("Abionic SA", "GSI", "portfolio"), ("Creoptix AG", "GSI", "exited"),
                          ("Augurix diagnostics", "GSI", "written_off"), ("Terapet SA", "LGV", "portfolio")])
        abionic, creoptix, augurix, terapet = report.positions
        self.assertEqual((abionic.cost, abionic.fair_value, abionic.invested_on), (D("437500.00"), D("43797.27"), date(2021, 11, 1)))
        self.assertEqual(terapet.invested_on, date(2022, 6, 1))                    # the LGV account column
        self.assertEqual(creoptix.invested_on, date(2021, 11, 9))                  # no company sheet: first closing
        self.assertTrue(any("Creoptix AG (GSI): investment date not found" in w for w in report.warnings))
        self.assertIsNone(augurix.cost)
        self.assertTrue(all("GSI + LGV" not in w for w in report.warnings))       # the combined section is not counted

    def test_totals_are_checked(self):
        book = workbook()
        ws = book["Portfolio Summary v3"]
        ws["K9"] = 999                                                             # the GSI total row says 999
        report = imp.read_positions(book, "Portfolio Summary v3")
        imp.check_totals(book, "Portfolio Summary v3", report)
        self.assertTrue(any("GSI total cost" in w and "745,565" in w for w in report.warnings))

    def test_apply_creates_positions_and_is_repeatable(self):
        report = imp.read_positions(workbook(), "Portfolio Summary v3")
        imp.apply_import(report, self.lgv1, "portfolio.xlsx")
        self.assertEqual((Company.objects.count(), Investment.objects.count(), Realization.objects.count()), (4, 3, 1))
        inv = Investment.objects.get(company__name="Abionic SA")
        self.assertEqual((inv.entity, inv.instrument, inv.amount, inv.currency, inv.reported_value, inv.reported_value_date),
                         (self.lgv1, "reported", D("437500.00"), "CHF", D("43797.27"), date(2025, 3, 17)))
        self.assertIn("[import:LGV1:GSI]", inv.notes)
        statuses = dict(Company.objects.values_list("name", "status"))
        self.assertEqual((statuses["Creoptix AG"], statuses["Augurix diagnostics"], statuses["Abionic SA"]),
                         ("exited", "written_off", "portfolio"))
        summary = company_summary(Company.objects.get(name="Abionic SA"), date(2025, 6, 30), fund=self.lgv1)
        self.assertEqual((summary.ccy, summary.invested_chf, summary.stake_value_chf, summary.moic),
                         ("CHF", D("437500.00"), D("43797.27"), D("0.10")))
        creoptix = company_summary(Company.objects.get(name="Creoptix AG"), date(2025, 6, 30), fund=self.lgv1)
        self.assertEqual(creoptix.realized_chf, D("335868.73"))
        # again: the same rows are updated, nothing is added
        again = imp.read_positions(workbook(), "Portfolio Summary v3")
        imp.apply_import(again, self.lgv1, "portfolio.xlsx")
        self.assertEqual((Company.objects.count(), Investment.objects.count(), Realization.objects.count()), (4, 3, 1))
        self.assertEqual((again.created, again.updated), (0, 3))

    def test_existing_company_is_reused_under_a_slightly_different_name(self):
        existing = Company.objects.create(name="Abionic", status="prospect")
        imp.apply_import(imp.read_positions(workbook(), "Portfolio Summary v3"), self.lgv1, "p.xlsx")
        self.assertEqual(Company.objects.filter(name__istartswith="Abionic").count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.status, "portfolio")

    def test_command_dry_run_then_apply(self):
        import os
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "portfolio.xlsx")
        workbook().save(path)
        out = StringIO()
        call_command("import_lgv1", path, stdout=out)
        self.assertIn("Dry run: 4 position(s) read, nothing written", out.getvalue())
        self.assertEqual(Investment.objects.count(), 0)
        out = StringIO()
        call_command("import_lgv1", path, "--apply", stdout=out)
        self.assertIn("Imported: 3 investment(s) created, 0 updated.", out.getvalue())
        self.assertEqual(Investment.objects.count(), 3)
